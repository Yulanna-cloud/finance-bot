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
# FAMILY
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
    for k, v in FAMILY_MEMBERS.items():
        if k in t:
            return v
    return ""

# =========================
# STORE (КРИТИЧЕСКИЙ FIX)
# =========================

def extract_store(text: str) -> str:
    t = text.lower()

    stores = [
        "пятерочка", "магнит", "лента", "вкусвилл",
        "spar", "дикси", "окей", "fix price",
        "перекресток", "ашан"
    ]

    for s in stores:
        if s in t:
            return s.title()

    return ""

# =========================
# CATEGORY RULES
# =========================

CATEGORY_RULES = {
    "Продукты": ["пятерочка","магнит","лента","вкусвилл","spar","дикси","окей"],
    "Кафе": ["кофе","кафе","бургер","пицца","ресторан","kfc"],
    "Транспорт": ["такси","яндекс","uber","азс","бензин"],
    "Жилье": ["аренда","жкх","квартира"],
    "Коммуналка": ["свет","газ","интернет","wifi"],
    "Медицина": ["аптека","врач","клиника"],
    "Обучение": ["курс","обучение","урок","танц","кружок","репетитор"],
    "Дети": ["детск","игрушк","lego"],
    "Красота": ["маникюр","салон","космет"],
    "Одежда": ["одежд","обув","wildberries","ozon"],
    "Доход": ["зарплата","аванс","доход","фриланс"],
    "Переводы": ["перевод","сбп"],
}

# =========================
# TEXT CLASSIFIER
# =========================

def classify_text(text: str) -> dict:
    t = text.lower()

    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', text)
    amounts = [float(a.replace(" ","").replace(",",".")) for a in amounts]
    amount = amounts[0] if amounts else 0

    op_type = "доход" if any(w in t for w in ["зарплата","доход","аванс","пришло"]) else "расход"

    family = extract_family_member(text)
    store = extract_store(text)

    category = "Прочее"
    for cat, keys in CATEGORY_RULES.items():
        if any(k in t for k in keys):
            category = cat
            break

    return {
        "тип": op_type,
        "сумма": amount,
        "категория": category,
        "подкатегория": "",
        "магазин": store,
        "описание": text,
        "получатель": family if op_type == "расход" else "",
        "отправитель": family if op_type == "доход" else "",
        "уверенность": 0.9
    }

# =========================
# CAPTION PARSER
# =========================

def parse_caption_instruction(caption: str):
    if not caption:
        return None

    t = caption.lower()

    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', caption)
    amount = None
    if amounts:
        try:
            amount = float(amounts[-1].replace(" ","").replace(",","."))

        except:
            pass

    return {
        "тип": "расход",
        "сумма": amount,
        "категория": "Прочее",
        "подкатегория": "",
        "магазин": extract_store(caption),
        "описание": caption,
        "получатель": extract_family_member(caption),
        "отправитель": "",
        "уверенность": 0.8,
        "source": "caption"
    }

# =========================
# BANK COMPATIBILITY (НЕ ЛОМАТЬ IMPORT)
# =========================

def parse_bank_statement(text: str):
    if not groq_client:
        return []

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":text[:4000]}]
        )

        raw = r.choices[0].message.content
        raw = raw.replace("```json","").replace("```","").strip()

        return json.loads(raw)

    except Exception as e:
        logger.error(f"bank parse error: {e}")
        return []

# =========================
# IMAGE
# =========================

def read_receipt_image(image_bytes: bytes, caption: str = None):
    if not groq_client:
        return {"ошибка":"нет GROQ","позиции":[]}

    caption_data = parse_caption_instruction(caption) if caption else None
    if caption_data and caption_data.get("сумма"):
        return caption_data

    image_b64 = base64.b64encode(image_bytes).decode()

    prompt = "Верни JSON чек"

    try:
        r = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role":"user",
                "content":[
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
                    {"type":"text","text":prompt}
                ]
            }]
        )

        raw = r.choices[0].message.content
        raw = raw.replace("```json","").replace("```","").strip()

        return json.loads(raw)

    except Exception as e:
        logger.error(f"image error: {e}")
        return {"ошибка":str(e),"позиции":[]}

# =========================
# VOICE
# =========================

def transcribe_voice(audio_bytes: bytes):
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
