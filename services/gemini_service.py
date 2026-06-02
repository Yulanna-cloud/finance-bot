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

GROCERY_STORES = CATEGORY_RULES["Продукты"]

# =========================
# CAPTION PARSER (ВАЖНО)
# =========================

def parse_caption_instruction(caption: str) -> dict:
    if not caption:
        return None

    text = caption.lower()

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
            result["сумма"] = float(amounts[-1].replace(" ","").replace(",","."))
        except:
            pass

    # семья
    fam = extract_family_member(caption)
    if fam:
        result["получатель"] = fam

    # категория
    for cat, keys in CATEGORY_RULES.items():
        if any(k in text for k in keys):
            result["категория"] = cat
            break

    return result

# =========================
# TEXT CLASSIFY
# =========================

def classify_text(text: str) -> dict:
    t = text.lower()

    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', text)
    amounts = [float(a.replace(" ","").replace(",",".")) for a in amounts]
    amount = amounts[0] if amounts else 0

    op_type = "доход" if any(w in t for w in ["зарплата","доход","аванс","пришло"]) else "расход"

    fam = extract_family_member(text)

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
        "магазин": "",
        "описание": text,
        "получатель": fam if op_type == "расход" else "",
        "отправитель": fam if op_type == "доход" else "",
        "уверенность": 0.9
    }

# =========================
# BANK STATEMENT (FIX IMPORT ERROR)
# =========================

def parse_bank_statement(text: str):
    """
    ВАЖНО: функция восстановлена для совместимости file_handler.py
    """
    if not groq_client:
        return []

    try:
        prompt = f"""
Разбери банковскую выписку в JSON массив:
{text[:4000]}
"""

        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}]
        )

        raw = r.choices[0].message.content
        raw = raw.replace("```json","").replace("```","").strip()

        return json.loads(raw)

    except Exception as e:
        logger.error(f"bank parse error: {e}")
        return []

# =========================
# IMAGE (SAFE VERSION)
# =========================

def read_receipt_image(image_bytes: bytes, caption: str = None):
    if not groq_client:
        return {"ошибка":"нет GROQ","позиции":[]}

    # если есть подпись — приоритет
    caption_data = parse_caption_instruction(caption) if caption else None
    if caption_data and caption_data.get("сумма"):
        return caption_data

    image_b64 = base64.b64encode(image_bytes).decode()

    prompt = """
Верни JSON:
{
  "магазин":"",
  "итого":0,
  "категории":[{"категория":"Продукты","сумма":0}]
}
"""

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
