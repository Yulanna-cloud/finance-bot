"""
Обработчик фото — чеки и QR-коды.
Поддерживает подпись к фото: отправь фото с текстом
"обучение Маргарите танцы" — бот использует это вместо автоопределения категории.
"""

import logging
import io
import os
import re
import json
import requests
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import groq_client, parse_caption_instruction
from services.sheets_service import write_operation, write_operations_batch

logger = logging.getLogger(__name__)

PROVERKACHEKA_TOKEN = os.environ.get("PROVERKACHEKA_TOKEN", "")

CATEGORY_KEYWORDS = {
    "Животные":      ["корм", "whiskas", "royal canin", "purina", "felix", "педигри", "pedigree"],
    "Бытовая химия": ["порошок", "гель", "фейри", "domestos", "туалетная бумага", "салфетки",
                      "мыло", "шампунь", "зубная", "щетка", "паста", "средство для"],
    "Красота":       ["крем", "тушь", "помада", "тени", "лак", "духи", "дезодорант"],
    "Табак":         ["сигарет", "табак", "вейп", "электронн"],
    "Алкоголь":      ["пиво", "вино", "водка", "коньяк", "шампанское"],
    "Кафе":          ["кофе", "латте", "капучино", "чай пакет"],
}

# ====== Таблица нормализации названия магазина ======
# Ключи — фрагменты которые могут встретиться в юрлице или названии
# Значения — правильное торговое название
STORE_NAME_MAP = [
    (["агроторг", "пятерочка", "пятёрочка", "pyaterochka", "5ка", "5-ка"], "Пятёрочка"),
    (["тандер", "магнит", "magnit"], "Магнит"),
    (["дикси", "dixi"], "Дикси"),
    (["перекресток", "перекрёсток", "perekrestok"], "Перекрёсток"),
    (["лента", "lenta"], "Лента"),
    (["вкусвилл", "вкус вилл", "vkusvill"], "ВкусВилл"),
    (["окей", "o'key", "okey"], "О'Кей"),
    (["светофор"], "Светофор"),
    (["монеточка", "monetochka"], "Монеточка"),
    (["самокат", "samokat"], "Самокат"),
    (["метро", "metro cash"], "Метро"),
    (["ашан", "auchan"], "Ашан"),
    (["глобус"], "Глобус"),
    (["спар", "spar"], "Спар"),
    (["fix price", "фикс прайс", "фикспрайс"], "Fix Price"),
    (["красное белое", "красное & белое"], "Красное&Белое"),
    (["бристоль"], "Бристоль"),
    (["пятёрочка экспресс", "пятерочка экспресс"], "Пятёрочка"),
]

def normalize_store_name(raw_name: str) -> str:
    """
    Принимает любое название магазина (юрлицо или торговое),
    возвращает нормальное торговое название.
    Если не распознано — убирает ООО/АО/ЗАО и возвращает очищенное.
    """
    if not raw_name:
        return ""

    name_lower = raw_name.lower().strip()

    # Убираем мусор: ООО, АО, ЗАО, кавычки, лишние пробелы
    cleaned = re.sub(
        r'\b(ооо|оао|зао|пао|ао|общество с ограниченной ответственностью|'
        r'акционерное общество|публичное акционерное общество)\b',
        '', name_lower
    )
    cleaned = re.sub(r'["""«»\']+', '', cleaned).strip()

    # Ищем совпадение в таблице
    for keywords, trade_name in STORE_NAME_MAP:
        for kw in keywords:
            if kw in name_lower or kw in cleaned:
                return trade_name

    # Если не нашли — возвращаем очищенное название с заглавной буквы
    result = cleaned.strip().title()
    return result if result else raw_name
# ====================================================


def classify_item(name: str) -> str:
    name_lower = name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return category
    return "Продукты"


def decode_qr_from_image(image_bytes: bytes) -> str | None:
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        codes = decode(img)
        for code in codes:
            text = code.data.decode("utf-8")
            logger.info(f"QR распознан: {text[:100]}")
            return text
        return None
    except ImportError:
        logger.warning("pyzbar не установлен")
        return None
    except Exception as e:
        logger.error(f"Ошибка декодирования QR: {e}")
        return None


def parse_qr_params(qr_text: str) -> dict | None:
    params = {}
    for pair in re.split(r'[&?]', qr_text):
        if '=' in pair:
            k, v = pair.split('=', 1)
            params[k.lower()] = v
    if 'fn' in params and 'i' in params and 'fp' in params:
        return params
    return None


def get_check_from_api(qr_params: dict) -> dict | None:
    try:
        url = "https://proverkacheka.com/api/v1/check/get"
        payload = {
            "token": PROVERKACHEKA_TOKEN,
            "qrraw": "&".join(f"{k}={v}" for k, v in qr_params.items()
                              if k in ["t", "s", "fn", "i", "fp", "n"]),
        }
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if data.get("code") == 1 and "data" in data:
            return data["data"].get("json", data["data"])
        return None
    except Exception as e:
        logger.error(f"Ошибка запроса к API: {e}")
        return None


def check_data_to_operations(check: dict, instruction: dict | None = None) -> tuple[list, str, float]:
    """
    Конвертирует данные чека в операции.
    Если передан instruction — применяет категорию/получателя из подписи.
    """
    items = check.get("items", [])
    store_raw = (check.get("user", "") or check.get("retailPlaceName", "") or "")
    store_raw = re.sub(r'"', '', store_raw).strip()[:40]
    store = normalize_store_name(store_raw)  # нормализуем

    date_raw = str(check.get("dateTime", "") or check.get("localDateTime", ""))
    op_date = ""
    if date_raw:
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_raw)
        if m:
            op_date = f"{m.group(3)}.{m.group(2)}.{m.group(1)}"

    total = float(check.get("totalSum", 0)) / 100

    if instruction and instruction.get("использовать_подпись"):
        operations = [{
            "дата": op_date,
            "сумма": total,
            "тип": "расход",
            "категория": instruction.get("категория", "Прочее"),
            "подкатегория": instruction.get("подкатегория", ""),
            "магазин": store or instruction.get("магазин", ""),
            "описание": instruction.get("описание", ""),
            "получатель": instruction.get("получатель", ""),
            "отправитель": "",
            "уверенность": 1.0,
        }]
        return operations, store, total

    operations = []
    for item in items:
        name = str(item.get("name", "Товар"))
        try:
            price = float(item.get("sum", 0)) / 100
        except (ValueError, TypeError):
            price = 0.0
        if price <= 0:
            continue
        operations.append({
            "дата": op_date,
            "сумма": price,
            "тип": "расход",
            "категория": classify_item(name),
            "подкатегория": "",
            "магазин": store,
            "описание": name,
            "получатель": "",
            "отправитель": "",
            "уверенность": 1.0,
        })

    return operations, store, total


def read_receipt_with_groq(image_bytes: bytes) -> dict | None:
    """
    Читает чек через Groq Vision.
    Возвращает словарь: {"магазин": "...", "позиции": [...]}
    Магазин дополнительно нормализуется через normalize_store_name.
    """
    if not groq_client:
        return None
    try:
        import base64
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        prompt = """Прочитай чек и верни ТОЛЬКО JSON без markdown.

Найди торговое название магазина (логотип крупно вверху чека).
Прочитай все позиции товаров с ценами.

{
  "магазин": "название магазина как написано на логотипе вверху чека",
  "позиции": [
    {"описание": "название товара", "сумма": цена_числом, "категория": "категория"},
    {"описание": "название товара", "сумма": цена_числом, "категория": "категория"}
  ]
}
Категории для позиций: Продукты, Животные, Бытовая химия, Красота, Табак, Алкоголь, Прочее
Не включай итого, скидки, бонусы — только отдельные товары с ценами."""

        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)

        # Если модель вернула список (старый формат) — оборачиваем
        if isinstance(result, list):
            return {"магазин": "", "позиции": result}

        if isinstance(result, dict):
            raw_store = result.get("магазин", "")
            # Нормализуем название магазина в коде — независимо от того, что вернула модель
            normalized_store = normalize_store_name(raw_store)
            logger.info(f"Магазин из модели: '{raw_store}' → нормализовано: '{normalized_store}'")
            return {
                "магазин": normalized_store,
                "позиции": result.get("позиции", [])
            }

        return None
    except Exception as e:
        logger.error(f"Ошибка Groq Vision: {e}")
        return None


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = (update.message.caption or "").strip()
    instruction = None
    if caption:
        instruction = parse_caption_instruction(caption)
        await update.message.reply_text(f"📝 Подпись: _{caption}_", parse_mode="Markdown")

    await update.message.reply_text("📷 Смотрю на фото...")

    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        # Шаг 1: QR-код
        qr_text = decode_qr_from_image(image_bytes)

        if qr_text:
            await update.message.reply_text("✅ QR-код найден! Запрашиваю данные чека...")
            qr_params = parse_qr_params(qr_text)
            if qr_params:
                check = get_check_from_api(qr_params)
                if check:
                    operations, store, total = check_data_to_operations(check, instruction)
                    if operations:
                        ok, errors = write_operations_batch(operations, source="чек_qr")
                        await _reply_receipt_result(update, operations, store, total, ok, instruction)
                        return
                    else:
                        await update.message.reply_text("⚠️ Чек получен, но позиции не распознаны.")
                        return
                else:
                    await update.message.reply_text("⚠️ QR найден, но данные не получены. Читаю фото...")
            else:
                await update.message.reply_text("⚠️ QR не похож на чек. Читаю фото...")

        # Шаг 2: Groq Vision
        await update.message.reply_text("🔍 Читаю чек по фото...")

        if instruction and instruction.get("сумма"):
            operations = [{
                "дата": "",
                "сумма": instruction["сумма"],
                "тип": "расход",
                "категория": instruction.get("категория", "Прочее"),
                "подкатегория": instruction.get("подкатегория", ""),
                "магазин": instruction.get("магазин", ""),
                "описание": instruction.get("описание", ""),
                "получатель": instruction.get("получатель", ""),
                "отправитель": "",
                "уверенность": 0.95,
            }]
            ok, _ = write_operations_batch(operations, source="чек_фото")
            await _reply_receipt_result(update, operations, "", instruction["сумма"], ok, instruction)
            return

        receipt_data = read_receipt_with_groq(image_bytes)

        if not receipt_data or not receipt_data.get("позиции"):
            if instruction:
                await update.message.reply_text(
                    "😔 Не смогла прочитать чек.\n"
                    "Напиши сумму отдельным сообщением, например:\n"
                    f"_{caption} 2400_",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    "😔 Не смогла прочитать чек.\n"
                    "Попробуй сфотографировать QR-код или запиши голосом/текстом."
                )
            return

        items = receipt_data.get("позиции", [])
        store_from_groq = receipt_data.get("магазин", "")

        operations = []
        for item in items:
            try:
                amount = float(str(item.get("сумма", 0)).replace(",", "."))
            except (ValueError, TypeError):
                continue
            if amount <= 0:
                continue

            if instruction and instruction.get("использовать_подпись"):
                cat = instruction.get("категория", "Прочее")
                subcat = instruction.get("подкатегория", "")
                recv = instruction.get("получатель", "")
                desc = instruction.get("описание") or item.get("описание", "")
                store = instruction.get("магазин", "") or store_from_groq
            else:
                cat = item.get("категория", "Продукты")
                subcat = ""
                recv = ""
                desc = item.get("описание", "")
                store = store_from_groq

            operations.append({
                "дата": "",
                "сумма": amount,
                "тип": "расход",
                "категория": cat,
                "подкатегория": subcat,
                "магазин": store,
                "описание": desc,
                "получатель": recv,
                "отправитель": "",
                "уверенность": 0.85,
            })

        if not operations:
            await update.message.reply_text("❌ Не нашла позиции с суммами.")
            return

        total = sum(op["сумма"] for op in operations)
        ok, errors = write_operations_batch(operations, source="чек_фото")
        await _reply_receipt_result(update, operations, store_from_groq, total, ok, instruction)

    except Exception as e:
        logger.error(f"Ошибка handle_photo: {e}", exc_info=True)
        await update.message.reply_text("❌ Что-то пошло не так при обработке фото.")


async def _reply_receipt_result(update, operations, store, total, ok, instruction):
    """Формирует ответ после обработки чека."""
    lines = []
    for op in operations[:15]:
        lines.append(f"• {op['описание'] or op['категория']} — {op['сумма']:,.0f} ₽")
    if len(operations) > 15:
        lines.append(f"_...и ещё {len(operations) - 15} позиций_")

    store_str = f"🏪 *{store}*\n\n" if store else ""
    note = ""
    if instruction and instruction.get("использовать_подпись"):
        cat = instruction.get("категория", "")
        recv = instruction.get("получатель", "")
        subcat = instruction.get("подкатегория", "")
        note_parts = [p for p in [cat, subcat, recv] if p]
        note = f"\n📂 {' / '.join(note_parts)}" if note_parts else ""

    await update.message.reply_text(
        f"🧾 Чек записан!\n\n{store_str}"
        + "\n".join(lines)
        + f"\n\n💰 Итого: *{total:,.0f} ₽*"
        + note
        + f"\n📥 Записано: {ok} позиций",
        parse_mode="Markdown"
    )
