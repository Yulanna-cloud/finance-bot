import logging
import re
import base64
import io
import os
import json
from groq import Groq

logger = logging.getLogger(__name__)

groq_api_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=groq_api_key) if groq_api_key else None

# =========================
# FAMILY
# =========================

FAMILY = {
    "маргарита": "Маргарита П.",
    "диана": "Диана Ш.",
    "алексей": "Алексей П.",
    "райса": "Райса Г.",
    "юланна": "Юланна Г."
}

def find_family(text):
    t = text.lower()
    for k, v in FAMILY.items():
        if k in t:
            return v
    return ""

# =========================
# STORES
# =========================

STORES = [
    "пятерочка","магнит","лента","вкусвилл",
    "spar","дикси","окей","перекресток","fix price"
]

def find_store(text):
    t = text.lower()
    for s in STORES:
        if s in t:
            return s.title()
    return ""

# =========================
# CATEGORY
# =========================

CATEGORIES = {
    "Продукты": STORES,
    "Кафе": ["кофе","кафе","ресторан","пицца","бургер"],
    "Транспорт": ["такси","яндекс","uber","азс","бензин"],
    "Жилье": ["аренда","жкх"],
    "Коммуналка": ["свет","газ","интернет"],
    "Обучение": ["курс","обучение","танц","урок","репетитор"],
    "Дети": ["детск","игрушк"],
    "Красота": ["маникюр","салон"],
    "Одежда": ["одежд","обув","ozon","wildberries"],
    "Доход": ["зарплата","доход","аванс"]
}

def find_category(text):
    t = text.lower()
    for cat, keys in CATEGORIES.items():
        for k in keys:
            if k in t:
                return cat
    return "Прочее"

# =========================
# CORE PARSER (ЕДИНАЯ ЛОГИКА)
# =========================

def normalize(text: str):
    if not text:
        return None

    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', text)
    amount = float(str(amounts[-1]).replace(" ","").replace(",", ".")) if amounts else 0

    op_type = "доход" if any(w in text.lower() for w in ["зарплата","доход","аванс","пришло"]) else "расход"

    return {
        "тип": op_type,
        "сумма": amount,
        "категория": find_category(text),
        "подкатегория": "",
        "магазин": find_store(text),
        "описание": text,
        "получатель": find_family(text) if op_type == "расход" else "",
        "отправитель": find_family(text) if op_type == "доход" else "",
        "уверенность": 1.0
    }

# =========================
# 1. ЭТО ВАЖНО — ВОССТАНАВЛИВАЕМ СТАРЫЕ API
# =========================

def classify_text(text: str):
    return normalize(text)

def parse_caption_instruction(caption: str):
    return normalize(caption)

def parse_caption(caption: str):
    return normalize(caption)

def parse_bank_statement(text: str):
    return normalize(text)

# =========================
# VOICE
# =========================

def transcribe_voice(audio_bytes: bytes):
    if not groq_client:
        return ""

    try:
        audio = io.BytesIO(audio_bytes)
        audio.name = "voice.ogg"

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
# IMAGE (без ломания логики)
# =========================

def read_receipt_image(image_bytes: bytes, caption: str = None):
    if caption:
        return normalize(caption)

    if not groq_client:
        return normalize("")

    try:
        img = base64.b64encode(image_bytes).decode()

        r = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role":"user",
                "content":[
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{img}"}},
                    {"type":"text","text":"Return JSON only"}
                ]
            }]
        )

        raw = r.choices[0].message.content
        raw = raw.replace("```json","").replace("```","").strip()

        try:
            json.loads(raw)
            return normalize(raw)
        except:
            return normalize(raw)

    except Exception as e:
        logger.error(f"image error: {e}")
        return normalize("")
