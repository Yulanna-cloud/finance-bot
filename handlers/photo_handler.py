"""
Обработчик фото — чеки и QR-коды.
Поддерживает подпись к фото: отправь фото с текстом
"обучение Маргарите танцы" — бот использует это вместо автоопределения категории.
Работает и с фото, и с изображением отправленным как файл (документ).
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
    "Аптека":        ["таблетк", "лекарств", "витамин", "мазь", "капли", "антибиотик", "анальгин",
                      "аспирин", "бинт", "вата", "пластырь", "шприц", "микстур", "сироп лекарств"],
    "Бытовая химия": ["порошок", "фейри", "domestos", "туалетная бумага", "салфетки",
                      "мыло", "шампунь", "зубная паста", "зубная щетка", "средство для"],
    "Красота":       ["крем", "тушь", "помада", "тени", "лак для ногтей", "духи", "дезодорант"],
    "Табак":         ["сигарет", "табак", "вейп", "электронн"],
    "Алкоголь":      ["пиво", "вино", "водка", "коньяк", "шампанское", "ликёр", "ликер"],
    "Кафе":          ["латте", "капучино", "американо", "эспрессо"],
}

STORE_NAME_MAP = [
    (["агроторг", "пятерочка", "пятёрочка", "pyaterochka", "5ка", "5-ка"], "Пятёрочка"),
    (["горздрав", "озерки", "ригла", "самсон", "планета здоровья", "доктор столетов",
      "zdravcity", "еаптека", "e-apteka", "аптека 24", "apteka"], "Аптека"),
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
    if not raw_name:
        return ""
    name_lower = raw_name.lower().strip()
    cleaned = re.sub(
        r'\b(ооо|оао|зао|пао|ао|общество с ограниченной ответственностью|'
        r'акционерное общество|публичное акционерное общество)\b',
        '', name_lower
    )
    cleaned = re.sub(r'["""«»\']+', '', cleaned).strip()
    for keywords, trade_name in STORE_NAME_MAP:
        for kw in keywords:
            if kw in name_lower or kw in cleaned:
                return trade_name
    result = cleaned.strip().title()
    return result if result else raw_name


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
    items = check.get("items", [])
    store_raw = (check.get("user", "") or check.get("retailPlaceName", "") or "")
    store_raw = re.sub(r'"', '', store_raw).strip()[:40]
    store = normalize_store_name(store_raw)

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
    if not groq_client:
        return None
    try:
        import base64
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        prompt = """Ты читаешь фотографию кассового чека из российского магазина.

СТРОГИЕ ПРАВИЛА:
1. Читай ТОЛЬКО то, что реально написано на чеке. НЕ ПРИДУМЫВАЙ товары, цены или название магазина.
2. Если чек повёрнут или плохо виден — читай как можешь, но только реальные слова с фото.
3. Используй цену из колонки "Итого" по строке (последнее число в строке товара), не цену за единицу.
4. В конце проверь: сумма позиций должна совпадать с числом в строке ИТОГ на чеке.

ВАЖНО — РАСШИФРОВКА СОКРАЩЕНИЙ:
На российских чеках названия товаров сильно сокращены. Определяй тип товара по смыслу:
- COL.G. / COLGATE / ORAL-B / LAPTA / BLEND-A-MED + "паста"/"щетка"/"gel" → Бытовая химия (зубная паста/щётка)
- LUCKY STRIKE / LM / MARLBORO / WINSTON / PARLIAMENT / "СИГАР" / "СИГ" / "CAR" + буквы → Табак (сигареты)
- ABSOLUT / HENNESSY / БЕЛУГА / "ВОД" / "КОНЬЯК" / "ВИНО" / "ПИВО" / "АЛК" → Алкоголь
- NIVEA / GARNIER / L'OREAL / ЧЕРНАЯ ЖЕМЧУЖИНА / "КРЕМ" / "ШАМПУНЬ" → Красота или Бытовая химия
- WHISKAS / PEDIGREE / PURINA / ROYAL CANIN / "КОРМ" → Животные
- Числовой код + буквы (типа "6715 ABC STR") — скорее всего сигареты → Табак
- Названия содержащие "ПАСТА Д/ЗУБ" / "ЗУБН" / "ПАСТА" рядом с брендом гигиены → Бытовая химия

Верни ТОЛЬКО JSON (без markdown, без пояснений):
{
  "магазин": "название магазина с логотипа вверху чека",
  "итого_на_чеке": число_из_строки_ИТОГ,
  "позиции": [
    {"описание": "точное название как написано на чеке", "сумма": итоговая_цена_числом, "категория": "категория"}
  ]
}

Категории:
- Продукты: еда, напитки, кофе/чай в упаковке, сладости, молочное, мясо, рыба, хлеб
- Аптека: лекарства, таблетки, витамины, медицинские товары
- Бытовая химия: зубная паста/щётка, порошок, моющие средства, туалетная бумага, мыло
- Табак: сигареты, табак, вейп (включая закодированные названия типа "6715 STR")
- Алкоголь: пиво, вино, водка, коньяк, шампанское
- Красота: косметика, духи, крем для лица/тела, тушь, помада
- Животные: корм для животных
- Кафе: готовые напитки в кафе (не упакованные товары)
- Прочее: всё остальное

НЕ включай в позиции: строки ИТОГО, скидки, НДС, бонусы, сдача — только реальные товары."""

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

        if isinstance(result, list):
            return {"магазин": "", "позиции": result}

        if isinstance(result, dict):
            raw_store = result.get("магазин", "")
            normalized_store = normalize_store_name(raw_store)
            logger.info(f"Магазин из модели: '{raw_store}' → нормализовано: '{normalized_store}'")

            positions = result.get("позиции", [])
            reported_total = result.get("итого_на_чеке")
            if reported_total and positions:
                try:
                    calc_total = sum(float(str(p.get("сумма", 0)).replace(",", ".")) for p in positions)
                    if reported_total > 0 and abs(calc_total - reported_total) / reported_total > 0.15:
                        logger.warning(
                            f"Сумма позиций ({calc_total:.2f}) сильно отличается от итога на чеке "
                            f"({reported_total:.2f}) — возможна ошибка распознавания"
                        )
                except Exception:
                    pass

            return {
                "магазин": normalized_store,
                "позиции": positions,
                "итого_на_чеке": reported_total,
            }

        return None
    except Exception as e:
        logger.error(f"Ошибка Groq Vision: {e}")
        return None


async def _download_image(update: Update, context) -> bytes | None:
    """
    Скачивает изображение — неважно как оно пришло: как фото или как файл-документ.
    Возвращает байты изображения или None если не удалось.
    """
    try:
        # Случай 1: пришло как обычное фото
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            buf = io.BytesIO()
            await file.download_to_memory(buf)
            return buf.getvalue()

        # Случай 2: пришло как документ (файл через скрепку)
        if update.message.document:
            doc = update.message.document
            file = await context.bot.get_file(doc.file_id)
            buf = io.BytesIO()
            await file.download_to_memory(buf)
            return buf.getvalue()

        return None
    except Exception as e:
        logger.error(f"Ошибка скачивания изображения: {e}")
        return None


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = (update.message.caption or "").strip()
    instruction = None
    if caption:
        instruction = parse_caption_instruction(caption)
        await update.message.reply_text(f"📝 Подпись: _{caption}_", parse_mode="Markdown")

    await update.message.reply_text("📷 Смотрю на фото...")

    try:
        # ====== ИСПРАВЛЕНО: скачиваем изображение независимо от способа отправки ======
        image_bytes = await _download_image(update, context)
        if not image_bytes:
            await update.message.reply_text("❌ Не удалось получить изображение.")
            return
        # ==============================================================================

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
