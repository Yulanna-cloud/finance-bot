import os
import json
import logging
import re
import base64
import io
from groq import Groq

logger = logging.getLogger(__name__)

groq_api_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=groq_api_key) if groq_api_key else None

# =========================
# СЕМЬЯ
# =========================

FAMILY_MEMBERS = {
    "маргарита": "Маргарита П.",
    "диана": "Диана Ш.",
    "алексей": "Алексей П.",
    "алёша": "Алексей П.",
    "алеша": "Алексей П.",
    "райса": "Райса Г.",
    "юланна": "Юланна Г.",
    "салават": "Салават Г.",
    "дамир": "Дамир Г.",
    "ольга": "Ольга Г.",
}

def extract_family_member(text: str) -> str:
    t = text.lower()
    for key, full in FAMILY_MEMBERS.items():
        if key in t:
            return full
    return ""

# =========================
# КАТЕГОРИИ
# =========================

CATEGORY_RULES = {
    "Продукты": ["пятерочка", "магнит", "лента", "вкусвилл", "spar", "дикси", "окей"],
    "Кафе": ["кофе", "кафе", "ресторан", "бургер", "пицца", "kfc", "latte", "espresso"],
    "Транспорт": ["такси", "uber", "яндекс", "азс", "бензин"],
    "Жилье": ["аренда", "жкх", "квартира"],
    "Коммуналка": ["свет", "газ", "интернет", "wifi"],
    "Медицина": ["аптека", "врач", "клиника"],
    "Обучение": ["курс", "обучение", "урок", "танц", "кружок", "репетитор"],
    "Дети": ["детск", "игрушк", "lego"],
    "Красота": ["салон", "маникюр", "космет"],
    "Одежда": ["одежд", "обув", "wildberries", "ozon"],
    "Доход": ["зарплата", "аванс", "доход", "фриланс"],
    "Переводы": ["перевод", "сбп"],
    "Прочее": []
}

GROCERY_STORES = ["пятерочка","магнит","лента","вкусвилл","spar","дикси","окей"]

# =========================
# CAPTION — ГЛАВНЫЙ FIX
# =========================

def parse_caption_instruction(caption: str) -> dict:
    if not caption:
        return None

    text_lower = caption.lower()

    result = {
        "тип": "расход",
        "сумма": None,
        "категория": "Прочее",
        "подкатегория": "",
        "магазин": "",
        "описание": caption,
        "получатель": "",
        "отправитель": "",
        "source": "caption"
    }

    # сумма
    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', caption)
    if amounts:
        try:
            result["сумма"] = float(amounts[-1].replace(" ", "").replace(",", "."))
        except:
            pass

    # семья
    family = extract_family_member(caption)
    if family:
        result["получатель"] = family

    # категория
    for cat, keys in CATEGORY_RULES.items():
        if any(k in text_lower for k in keys):
            result["категория"] = cat
            break

    # подкатегория
    sub = {
        "танц": "Танцы",
        "англ": "Английский",
        "математ": "Математика",
        "рисов": "Рисование",
        "спорт": "Спорт"
    }

    for k, v in sub.items():
        if k in text_lower:
            result["подкатегория"] = v
            break

    return result

# =========================
# IMAGE OCR (FIX: caption override)
# =========================

def read_receipt_image(image_bytes: bytes, caption: str = None) -> dict:
    """
    FIX:
    caption теперь влияет на итог.
    """
    if not groq_client:
        return {"ошибка": "нет GROQ", "позиции": []}

    # 🔥 если есть подпись — она ПРИОРИТЕТ
    caption_data = parse_caption_instruction(caption) if caption else None

    if caption_data and caption_data.get("сумма"):
        return caption_data

    image_b64 = base64.b64encode(image_bytes).decode()

    prompt = f"""
Верни ТОЛЬКО JSON.

Если есть текстовая подсказка пользователя — учитывай её:
{caption if caption else "нет"}

Формат:
{{
  "магазин": "",
  "итого": 0,
  "категории": [
    {{"категория": "Продукты", "сумма": 0}}
  ]
}}
"""

    try:
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

        raw = response.choices[0].message.content
        raw = raw.replace("```json","").replace("```","").strip()

        data = json.loads(raw)

        # merge caption if exists
        if caption_data:
            data["caption_override"] = caption_data

        return data

    except Exception as e:
        logger.error(f"receipt error: {e}")
        return {"ошибка": str(e), "позиции": []}

# =========================
# TEXT CLASSIFIER FIX
# =========================

def classify_text(text: str) -> dict:
    text_lower = text.lower()

    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', text)
    amounts = [float(a.replace(" ","").replace(",", ".")) for a in amounts]
    amount = amounts[0] if amounts else 0.0

    op_type = "доход" if any(w in text_lower for w in ["зарплата","аванс","пришло","доход"]) else "расход"

    family = extract_family_member(text)

    # категория
    category = "Прочее"
    for cat, keys in CATEGORY_RULES.items():
        if any(k in text_lower for k in keys):
            category = cat
            break

    return {
        "тип": op_type,
        "сумма": amount,
        "категория": category,
        "подкатегория": "",
        "магазин": "",
        "описание": text,
        "получатель": family if op_type == "расход" else "",
        "отправитель": family if op_type == "доход" else "",
        "уверенность": 0.9,
        "source": "text"
    }

# =========================
# VOICE
# =========================

def transcribe_voice(audio_bytes: bytes) -> str:
    if not groq_client:
        return ""

    audio = io.BytesIO(audio_bytes)
    audio.name = "voice.ogg"

    try:
        r = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio,
            language="ru"
        )
        return r.text.strip()
    except Exception as e:
        logger.error(f"voice error: {e}")
        return ""

# =========================
# DEFAULT
# =========================

def _default(text, amount=0):
    return {
        "тип": "расход",
        "сумма": amount,
        "категория": "Прочее",
        "подкатегория": "",
        "магазин": "",
        "описание": text,
        "получатель": "",
        "отправитель": "",
        "уверенность": 0.2
    }
