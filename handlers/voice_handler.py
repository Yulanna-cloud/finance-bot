"""
Обработчик голосовых сообщений.
"""
import logging
import io
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import transcribe_voice, classify_text
from services.sheets_service import write_operation

logger = logging.getLogger(__name__)


async def _send_multi(update, result, source):
    """Записывает несколько позиций из детального голосового ввода."""
    позиции = result.get("позиции", [])
    магазин = result.get("магазин", "")
    тип = result.get("тип", "расход")

    if not позиции:
        await update.message.reply_text("🤔 Не смогла разобрать позиции.")
        return

    lines = []
    total = 0
    for p in позиции:
        op = {
            "тип": тип,
            "сумма": float(p.get("сумма", 0)),
            "категория": p.get("категория", "Прочее"),
            "подкатегория": p.get("подкатегория", ""),
            "магазин": магазин,
            "описание": p.get("описание", ""),
            "уверенность": 0.9
        }
        write_operation(op, source=source)
        total += op["сумма"]
        lines.append(f"• {op['описание']} — {op['сумма']:.0f} ₽ ({op['категория']})")

    await update.message.reply_text(
        f"💸 Записано {len(позиции)} позиций!\n\n"
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

        result = classify_text(transcribed)

        # Детальный ввод с несколькими позициями
        if result.get("мультизапись"):
            await _send_multi(update, result, source="голос")
            return

        if not result.get("сумма"):
            await update.message.reply_text(
                "🤔 Не нашла сумму. Скажи, например: *«потратила на кофе триста пятьдесят»*",
                parse_mode="Markdown"
            )
            return

        result["исходный_текст"] = transcribed
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
        logger.error(f"Ошибка handle_voice: {e}")
        await update.message.reply_text("❌ Что-то пошло не так с голосовым сообщением.")
