"""
Обработчик фото — чеки и QR-коды.
Если на фото QR-код чека — читает через API ФНС (proverkacheka.com).
Если обычное фото чека — читает через Groq Vision.
"""

import logging
import io
import os
import re
import json
import requests
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import groq_client
from services.sheets_service import write_operations_batch

logger = logging.getLogger(__name__)

PROVERKACHEKA_TOKEN = os.environ.get("PROVERKACHEKA_TOKEN", "39711.1Nv7hzEHi9n7ROzty")

# Категории по ключевым словам в названии товара
CATEGORY_KEYWORDS = {
    "Животные":      ["корм", "whiskas", "royal canin", "purina", "felix", "педигри", "pedigree"],
    "Бытовая химия": ["порошок", "гель", "фейри", "domestos", "туалетная бумага", "салфетки",
                      "мыло", "шампунь", "зубная", "щетка", "паста", "средство для"],
    "Красота":       ["крем", "тушь", "помада", "тени", "лак", "духи", "дезодорант"],
    "Табак":         ["сигарет", "табак", "вейп", "электронн"],
    "Алкоголь":      ["пиво", "вино", "водка", "коньяк", "шампанское", "пивн"],
    "Кафе":          ["кофе", "латте", "капучино", "чай пакет"],
}

def classify_item(name: str) -> str:
    name_lower = name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return category
    return "Продукты"


def decode_qr_from_image(image_bytes: bytes) -> str | None:
    """Распознаёт QR-код из изображения через pyzbar."""
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
        logger.warning("pyzbar не установлен — QR не читается")
        return None
    except Exception as e:
        logger.error(f"Ошибка декодирования QR: {e}")
        return None


def parse_qr_params(qr_text: str) -> dict | None:
    """
    Извлекает параметры чека из QR-строки.
    Формат: t=20231015T1200&s=1234.56&fn=1234567890&i=12345&fp=1234567890&n=1
    """
    params = {}
    # Парсим как URL query string
    for pair in re.split(r'[&?]', qr_text):
        if '=' in pair:
            k, v = pair.split('=', 1)
            params[k.lower()] = v
    if 'fn' in params and 'i' in params and 'fp' in params:
        return params
    return None


def get_check_from_api(qr_params: dict) -> dict | None:
    """Получает данные чека через proverkacheka.com API."""
    try:
        url = "https://proverkacheka.com/api/v1/check/get"
        payload = {
            "token": PROVERKACHEKA_TOKEN,
            "qrraw": "&".join(f"{k}={v}" for k, v in qr_params.items()
                              if k in ["t", "s", "fn", "i", "fp", "n"]),
        }
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        logger.info(f"API ответ: code={data.get('code')}")

        if data.get("code") == 1 and "data" in data:
            return data["data"].get("json", data["data"])
        else:
            logger.error(f"API ошибка: {data}")
            return None
    except Exception as e:
        logger.error(f"Ошибка запроса к API: {e}")
        return None


def check_data_to_operations(check: dict) -> tuple[list, str, float]:
    """
    Конвертирует данные чека в список операций для таблицы.
    Возвращает (операции, название_магазина, итого).
    """
    items = check.get("items", [])
    store = (
        check.get("user", "") or
        check.get("retailPlaceName", "") or
        check.get("userInn", "")
    )
    # Чистим название магазина
    store = re.sub(r'"', '', store).strip()
    if len(store) > 40:
        store = store[:40]

    date_raw = str(check.get("dateTime", "") or check.get("localDateTime", ""))
    # Формат даты из API: "2023-10-15T12:00:00"
    op_date = ""
    if date_raw:
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_raw)
        if m:
            op_date = f"{m.group(3)}.{m.group(2)}.{m.group(1)}"

    total = float(check.get("totalSum", 0)) / 100  # копейки → рубли

    operations = []
    for item in items:
        name = str(item.get("name", "Товар"))
        # Сумма в копейках
        try:
            price = float(item.get("sum", 0)) / 100
        except (ValueError, TypeError):
            price = 0.0
        if price <= 0:
            continue

        category = classify_item(name)
        operations.append({
            "дата": op_date,
            "сумма": price,
            "тип": "расход",
            "категория": category,
            "подкатегория": "",
            "магазин": store,
            "описание": name,
            "получатель": "",
            "отправитель": "",
            "уверенность": 1.0,
        })

    return operations, store, total


def read_receipt_with_groq(image_bytes: bytes) -> list | None:
    """Читает чек через Groq Vision если QR не найден."""
    if not groq_client:
        return None
    try:
        import base64
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = """Прочитай чек. Верни ТОЛЬКО JSON массив без markdown.
Каждый элемент: {"описание":"название товара","сумма":число,"категория":"категория"}
Категории: Продукты, Животные, Бытовая химия, Красота, Табак, Алкоголь, Прочее
Не включай итого, скидки, бонусы — только отдельные товары."""

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
        raw = response.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
        items = json.loads(raw)
        if isinstance(items, list):
            return items
        return None
    except Exception as e:
        logger.error(f"Ошибка Groq Vision: {e}")
        return None


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📷 Смотрю на фото...")

    try:
        # Берём фото наилучшего качества
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        # ── Шаг 1: пробуем распознать QR-код ─────────────────────────────
        qr_text = decode_qr_from_image(image_bytes)

        if qr_text:
            await update.message.reply_text("✅ QR-код найден! Запрашиваю данные чека...")
            logger.info(f"QR текст: {qr_text}")

            qr_params = parse_qr_params(qr_text)
            if qr_params:
                check = get_check_from_api(qr_params)
                if check:
                    operations, store, total = check_data_to_operations(check)
                    if operations:
                        ok, errors = write_operations_batch(operations, source="чек_qr")
                        lines = [f"• {op['описание']} — {op['сумма']:.2f} ₽ ({op['категория']})"
                                 for op in operations[:15]]
                        if len(operations) > 15:
                            lines.append(f"_...и ещё {len(operations)-15} позиций_")
                        store_str = f"🏪 *{store}*\n\n" if store else ""
                        await update.message.reply_text(
                            f"🧾 Чек прочитан!\n\n{store_str}"
                            + "\n".join(lines)
                            + f"\n\n💰 Итого: *{total:.2f} ₽*\n"
                            + f"📥 Записано: {ok} позиций",
                            parse_mode="Markdown"
                        )
                        return
                    else:
                        await update.message.reply_text("⚠️ Чек получен, но позиции не распознаны.")
                        return
                else:
                    await update.message.reply_text(
                        "⚠️ QR найден, но не удалось получить данные от ФНС.\n"
                        "Попробую прочитать фото напрямую..."
                    )
            else:
                await update.message.reply_text(
                    "⚠️ QR-код не похож на чек.\n"
                    "Попробую прочитать фото напрямую..."
                )

        # ── Шаг 2: читаем фото через Groq Vision ─────────────────────────
        await update.message.reply_text("🔍 Читаю чек по фото...")
        items = read_receipt_with_groq(image_bytes)

        if not items:
            await update.message.reply_text(
                "😔 Не смогла прочитать чек.\n"
                "Попробуй:\n"
                "• Сфотографировать QR-код крупнее\n"
                "• Или записать голосом"
            )
            return

        operations = []
        for item in items:
            try:
                amount = float(str(item.get("сумма", 0)).replace(",", "."))
            except (ValueError, TypeError):
                continue
            if amount <= 0:
                continue
            operations.append({
                "дата": "",
                "сумма": amount,
                "тип": "расход",
                "категория": item.get("категория", "Продукты"),
                "подкатегория": "",
                "магазин": "",
                "описание": item.get("описание", ""),
                "получатель": "",
                "отправитель": "",
                "уверенность": 0.85,
            })

        if not operations:
            await update.message.reply_text("❌ Не нашла позиции с суммами.")
            return

        ok, errors = write_operations_batch(operations, source="чек_фото")
        total = sum(op["сумма"] for op in operations)
        lines = [f"• {op['описание']} — {op['сумма']:.2f} ₽ ({op['категория']})"
                 for op in operations[:15]]
        if len(operations) > 15:
            lines.append(f"_...и ещё {len(operations)-15} позиций_")

        await update.message.reply_text(
            f"🧾 Чек прочитан!\n\n"
            + "\n".join(lines)
            + f"\n\n💰 Итого: *{total:.2f} ₽*\n"
            + f"📥 Записано: {ok} позиций",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Ошибка handle_photo: {e}", exc_info=True)
        await update.message.reply_text("❌ Что-то пошло не так при обработке фото.")
