"""
Обработчик голосовых сообщений.
"""
import logging
import io
import json
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import transcribe_voice, classify_text, groq_client
from services.sheets_service import write_operation, write_operations_batch

logger = logging.getLogger(__name__)


def normalize_numbers(text: str) -> str:
    """
    Конвертирует числа-слова в цифры через Groq.
    'два по двадцать один рубль' -> '2 по 21 рубль'
    """
    if not groq_client:
        return text
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    f"Замени все числа-слова на цифры в этом тексте. "
                    f"Верни ТОЛЬКО исправленный текст, без пояснений.\n\n{text}"
                )
            }]
        )
        result = response.choices[0].message.content.strip()
        logger.info(f"Нормализация чисел: '{text}' -> '{result}'")
        return result
    except Exception as e:
        logger.error(f"Ошибка normalize_numbers: {e}")
        return text


def parse_multi_items(text: str) -> list | None:
    """
    Разбирает текст с несколькими покупками через Groq.
    Возвращает список позиций или None если не удалось.
    """
    if not groq_client:
        return None
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    f"Разбери список покупок и верни ТОЛЬКО JSON массив без markdown.\n"
                    f"Каждый элемент: {{\"описание\":\"название товара\",\"сумма\":число,\"категория\":\"категория\",\"количество\":1}}\n\n"
                    f"Категории: Продукты, Животные, Бытовая химия, Красота, Одежда, Кафе, Транспорт, Прочее\n\n"
                    f"Правила:\n"
                    f"- Если товар в нескольких штуках (2 штуки по 21 руб) — создай ОДНУ запись с суммой = кол-во × цена\n"
                    f"- Корм для животных → категория Животные\n"
                    f"- Молоко, сметана, хлеб, макароны → категория Продукты\n"
                    f"- Доставка → категория Прочее\n\n"
                    f"Текст: {text}"
                )
            }]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        items = json.loads(raw)
        if isinstance(items, list) and len(items) > 0:
            return items
        return None
    except Exception as e:
        logger.error(f"Ошибка parse_multi_items: {e}")
        return None


def has_multiple_items(text: str) -> bool:
    """Определяет, содержит ли текст несколько покупок."""
    import re
    # Несколько сумм в тексте (цифры или слова-числа)
    digit_amounts = re.findall(r'\d+\s*(?:рубл|руб|₽)', text.lower())
    word_numbers = ["рубль", "рублей", "рубля"]
    word_count = sum(text.lower().count(w) for w in word_numbers)
    total_amounts = len(digit_amounts) + word_count
    return total_amounts > 1


async def _send_multi_items(update, items: list, магазин: str, source: str):
    """Записывает несколько позиций в таблицу."""
    if not items:
        await update.message.reply_text("🤔 Не смогла разобрать позиции.")
        return

    operations = []
    for p in items:
        try:
            amount = float(str(p.get("сумма", 0)).replace(",", "."))
        except (ValueError, TypeError):
            continue
        if amount <= 0:
            continue
        operations.append({
            "тип": "расход",
            "сумма": amount,
            "категория": p.get("категория", "Прочее"),
            "подкатегория": "",
            "магазин": магазин,
            "описание": p.get("описание", ""),
            "получатель": "",
            "отправитель": "",
            "уверенность": 0.85,
        })

    if not operations:
        await update.message.reply_text("❌ Не нашла суммы в позициях.")
        return

    ok, errors = write_operations_batch(operations, source=source)
    total = sum(op["сумма"] for op in operations)

    lines = []
    for op in operations:
        lines.append(f"• {op['описание']} — {op['сумма']:.0f} ₽ ({op['категория']})")

    store_str = f"🏪 {магазин}\n\n" if магазин else ""
    await update.message.reply_text(
        f"💸 Записано {ok} позиций!\n\n"
        f"{store_str}"
        + "\n".join(lines)
        + f"\n\n💰 Итого: *{total:.0f} ₽*",
        parse_mode="Markdown"
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Слушаю...")

    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        audio_bytes = buf.getvalue()

        transcribed = transcribe_voice(audio_bytes, mime_type="audio/ogg")
        if not transcribed:
            await update.message.reply_text(
                "🤔 Не смогла расшифровать. Попробуй говорить чуть медленнее или напиши текстом."
            )
            return

        await update.message.reply_text(f"📝 Услышала: _{transcribed}_", parse_mode="Markdown")

        # Если несколько покупок — разбираем детально
        if has_multiple_items(transcribed):
            await update.message.reply_text("🔍 Вижу несколько позиций, разбираю...")

            # Нормализуем числа-слова в цифры
            normalized = normalize_numbers(transcribed)

            # Определяем магазин из текста
            import re
            магазин = ""
            store_patterns = ["пятерочка", "магнит", "лента", "перекресток", "вкусвилл", "самокат"]
            for store in store_patterns:
                if store in normalized.lower():
                    магазин = store.title()
                    break

            items = parse_multi_items(normalized)

            if items:
                await _send_multi_items(update, items, магазин, source="голос")
                return
            # Если не получилось разобрать — падаем на обычный режим

        # Одна покупка — стандартная обработка
        normalized = normalize_numbers(transcribed)
        result = classify_text(normalized)

        # Старая мультизапись (из classify_text)
        if result.get("мультизапись"):
            позиции = result.get("позиции", [])
            магазин = result.get("магазин", "")
            ops = []
            for p in позиции:
                try:
                    amount = float(p.get("сумма", 0))
                except (ValueError, TypeError):
                    continue
                if amount <= 0:
                    continue
                ops.append({
                    "тип": "расход",
                    "сумма": amount,
                    "категория": p.get("категория", "Прочее"),
                    "подкатегория": "",
                    "магазин": магазин,
                    "описание": p.get("описание", ""),
                    "получатель": "",
                    "отправитель": "",
                    "уверенность": 0.9,
                })
            if ops:
                ok, _ = write_operations_batch(ops, source="голос")
                total = sum(o["сумма"] for o in ops)
                lines = [f"• {o['описание']} — {o['сумма']:.0f} ₽ ({o['категория']})" for o in ops]
                store_str = f"🏪 {магазин}\n\n" if магазин else ""
                await update.message.reply_text(
                    f"💸 Записано {ok} позиций!\n\n{store_str}" + "\n".join(lines) + f"\n\n💰 Итого: *{total:.0f} ₽*",
                    parse_mode="Markdown"
                )
                return

        if not result.get("сумма"):
            await update.message.reply_text(
                "🤔 Не нашла сумму. Скажи, например: *«потратила на кофе триста пятьдесят»*",
                parse_mode="Markdown"
            )
            return

        result["исходный_текст"] = transcribed
        result.setdefault("получатель", "")
        result.setdefault("отправитель", "")
        ok = write_operation(result, source="голос")

        if ok:
            emoji = "💸" if result.get("тип") == "расход" else "💰"
            cat = result.get("категория", "Прочее")
            subcat = result.get("подкатегория", "")
            subcat_str = f" / {subcat}" if subcat else ""
            store = result.get("магазин", "")
            store_str = f"\n🏪 {store}" if store else ""
            confidence = result.get("уверенность", 0)
            warning = "\n⚠️ _Низкая уверенность — проверь в таблице_" if confidence < 0.8 else ""
            await update.message.reply_text(
                f"{emoji} Записано!\n\n"
                f"💰 *{result['сумма']:.0f} ₽*\n"
                f"📂 {cat}{subcat_str}{store_str}"
                f"{warning}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ Не удалось записать в таблицу.")

    except Exception as e:
        logger.error(f"Ошибка handle_voice: {e}", exc_info=True)
        await update.message.reply_text("❌ Что-то пошло не так с голосовым сообщением.")
