import os
import json
import logging
import re
import base64
import io
from google import genai
from google.genai import types
from groq import Groq

logger = logging.getLogger(__name__)

# Gemini (для категоризации текста — не требует изображений)
gemini_api_key = os.getenv("GEMINI_API_KEY")
gemini_client = None
if gemini_api_key:
    gemini_client = genai.Client(api_key=gemini_api_key)
else:
    logger.error("GEMINI_API_KEY не найден!")

# Groq для голоса и чеков
groq_api_key = os.getenv("GROQ_API_KEY")
groq_client = None
if groq_api_key:
    groq_client = Groq(api_key=groq_api_key)
else:
    logger.error("GROQ_API_KEY не найден!")

CATEGORY_RULES = {
    "Продукты": [
        "пятерочка", "магнит", "вкусвилл", "перекресток",
        "лента", "ашан", "самокат", "яндекс лавка", "spar",
        "fix price", "дикси", "окей"
    ],
    "Кафе": [
        "кофе", "кафе", "ресторан", "шаверма", "бургер",
        "суши", "пицца", "starbucks", "surf coffee",
        "вкусно и точка", "kfc", "burger king", "капучино",
        "латте", "эспрессо", "фастфуд"
    ],
    "Транспорт": [
        "бензин", "заправка", "лукойл", "роснефть", "азс",
        "такси", "автобус", "uber", "яндекс go", "яндекс го"
    ],
    "Жилье": ["жкх", "коммуналка", "аренда", "квартира"],
    "Коммуналка": ["свет", "электричество", "интернет", "wifi", "газ"],
    "Медицина": ["аптека", "врач", "клиника", "таблетки", "витамины", "36.6", "ригла"],
    "Дети": ["детск", "игрушк", "секци", "кружок", "школ", "lego"],
    "Животные": ["корм", "ветеринар", "whiskas", "royal canin"],
    "Красота": ["салон", "парикмахер", "маникюр", "косметик"],
    "Одежда": ["одежда", "обувь", "куртка", "кроссовк", "wildberries", "ozon"],
    "Развлечения": ["кино", "театр", "концерт", "музей"],
    "Подписки": ["яндекс плюс", "yandex plus", "netflix", "spotify", "icloud"],
    "Доход": ["зарплата", "аванс", "оклад", "фриланс", "подработка"],
    "Переводы": ["перевод", "сбп"],
}


def classify_text(text: str) -> dict:
    text_lower = text.lower()

    amount_match = re.search(r'(\d[\d\s]*\d|\d+)', text)
    amount = 0.0
    if amount_match:
        raw = amount_match.group(0).replace(" ", "")
        try:
            amount = float(raw)
        except ValueError:
            pass

    op_type = "доход" if any(w in text_lower for w in ["зарплата", "аванс", "доход", "получил", "получила"]) else "расход"

    for category, keywords in CATEGORY_RULES.items():
        for keyword in keywords:
            if keyword in text_lower:
                subcat = get_subcat(category, text_lower)
                return {
                    "тип": op_type,
                    "сумма": amount,
                    "категория": category,
                    "подкатегория": subcat,
                    "магазин": keyword.title(),
                    "описание": text,
                    "уверенность": 0.95
                }

    if not groq_client:
        return _default(text, amount, op_type)

    prompt = f"""Ты помощник для учёта финансов. Верни ТОЛЬКО JSON без markdown.

Операция: "{text}"

Категории (точно одну из них):
Продукты, Кафе, Транспорт, Жилье, Коммуналка, Медицина, Дети,
Животные, Красота, Одежда, Развлечения, Подписки, Доход, Переводы, Прочее

{{
  "тип": "расход",
  "сумма": {amount},
  "категория": "...",
  "подкатегория": null,
  "магазин": null,
  "описание": "...",
  "уверенность": 0.7
}}"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        if not result.get("сумма") and amount:
            result["сумма"] = amount
        return result
    except Exception as e:
        logger.error(f"Ошибка Groq classify: {e}")
        return _default(text, amount, op_type)


def get_subcat(category: str, text_lower: str) -> str:
    subcats = {
        "Продукты": {
            "молочка": ["молоко", "кефир", "йогурт", "сыр", "творог"],
            "мясо": ["мясо", "курица", "говядина", "фарш"],
            "хлеб": ["хлеб", "батон", "булка"],
            "бытовая химия": ["порошок", "мыло", "шампунь", "гель"],
        },
        "Кафе": {
            "кофе": ["кофе", "капучино", "латте", "эспрессо"],
            "фастфуд": ["бургер", "kfc", "вкусно", "шаверма"],
            "рестораны": ["ресторан", "суши", "пицца"],
        },
        "Транспорт": {
            "бензин": ["бензин", "заправка", "лукойл", "роснефть", "азс"],
            "такси": ["такси", "uber", "яндекс go", "яндекс го"],
            "метро": ["метро", "автобус", "проездной"],
        },
    }
    if category in subcats:
        for subcat, words in subcats[category].items():
            if any(w in text_lower for w in words):
                return subcat
    return ""


def _default(text, amount, op_type):
    return {
        "тип": op_type, "сумма": amount, "категория": "Прочее",
        "подкатегория": "", "магазин": "", "описание": text, "уверенность": 0.3
    }


def transcribe_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    if not groq_client:
        logger.error("GROQ_API_KEY не найден")
        return ""
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "voice.ogg"
        transcription = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio_file,
            language="ru"
        )
        text = transcription.text.strip()
        logger.info(f"Расшифровка голоса (Groq): {text}")
        return text
    except Exception as e:
        logger.error(f"Ошибка голоса Groq: {e}")
        return ""


def read_receipt_image(image_bytes: bytes) -> dict:
    if not groq_client:
        return {"ошибка": "нет GROQ_API_KEY", "позиции": []}
    try:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = """Прочитай чек на изображении, верни ТОЛЬКО JSON без markdown:
{"магазин":"название","дата":"дата","итого":сумма,"позиции":[{"название":"товар","сумма":число,"категория":"Продукты","подкатегория":"молочка"}]}
Молоко/кефир/йогурт→молочка, Порошок/мыло/шампунь→бытовая химия, Хлеб/батон→хлеб, Мясо/курица→мясо.
Категории: Продукты, Кафе, Транспорт, Медицина, Красота, Одежда, Прочее."""
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Ошибка чека Groq: {e}")
        return {"ошибка": str(e), "позиции": []}


def parse_bank_statement(text: str) -> list:
    if not groq_client:
        return []
    try:
        prompt = f"""Разбери выписку, верни ТОЛЬКО JSON массив без markdown:
[{{"дата":"DD.MM.YYYY","сумма":число,"тип":"расход/доход","категория":"...","магазин":"...","описание":"...","уверенность":0.8}}]
Категории: Продукты, Кафе, Транспорт, Жилье, Коммуналка, Медицина, Одежда, Развлечения, Подписки, Доход, Прочее
Выписка:
{text[:4000]}"""
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Ошибка выписки Groq: {e}")
        return []
