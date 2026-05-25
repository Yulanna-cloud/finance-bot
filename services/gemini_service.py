import os
import json
import logging
import re
import google.generativeai as genai

logger = logging.getLogger(__name__)

# ==========================================
# GEMINI API
# ==========================================

api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)
else:
    logger.error("GEMINI_API_KEY не найден!")

# ==========================================
# ПРАВИЛА КАТЕГОРИЙ
# ==========================================

CATEGORY_RULES = {
    "Продукты": [
        "пятерочка",
        "магнит",
        "вкусвилл",
        "перекресток",
        "лента",
        "ашан",
        "самокат",
        "яндекс лавка",
        "spar",
        "fix price"
    ],

    "Кафе": [
        "кофе",
        "кафе",
        "ресторан",
        "шаверма",
        "бургер",
        "суши",
        "пицца"
    ],

    "Авто": [
        "бензин",
        "заправка",
        "лукойл",
        "роснефть"
    ],

    "Транспорт": [
        "такси",
        "метро",
        "автобус",
        "uber",
        "яндекс go"
    ],

    "Жилье": [
        "жкх",
        "коммуналка",
        "аренда"
    ],

    "Здоровье": [
        "аптека",
        "врач"
    ]
}

# ==========================================
# AI КЛАССИФИКАЦИЯ
# ==========================================

def classify_text(text: str) -> dict:

    text_lower = text.lower()

    # ======================================
    # СУММА
    # ======================================

    amount_match = re.search(r'(\d+[.,]?\d*)', text)

    amount = 0

    if amount_match:
        amount = float(amount_match.group(1).replace(",", "."))

    # ======================================
    # ЖЕСТКИЕ ПРАВИЛА
    # ======================================

    for category, keywords in CATEGORY_RULES.items():

        for keyword in keywords:

            if keyword in text_lower:

                return {
                    "тип": "расход",
                    "сумма": amount,
                    "валюта": "RUB",
                    "категория": category,
                    "подкатегория": "",
                    "магазин": keyword.title(),
                    "описание": text,
                    "уверенность": 0.95
                }

    # ======================================
    # FALLBACK GEMINI
    # ======================================

    default_response = {
        "тип": "расход",
        "сумма": amount,
        "валюта": "RUB",
        "категория": "Прочее",
        "подкатегория": "",
        "магазин": "",
        "описание": text,
        "уверенность": 0.30
    }

    if not api_key:
        return default_response

    prompt = f"""
Верни только JSON.

Категории:
Продукты
Кафе
Авто
Транспорт
Жилье
Развлечения
Здоровье
Одежда
Прочее

Сообщение:
{text}
"""

    try:

        model = genai.GenerativeModel("gemini-1.5-flash")

        response = model.generate_content(prompt)

        raw = response.text.strip()

        raw = raw.replace("```json", "")
        raw = raw.replace("```", "")
        raw = raw.strip()

        result = json.loads(raw)

        result["уверенность"] = 0.70

        return result

    except Exception as e:

        logger.error(f"Ошибка Gemini: {e}")

        return default_response

# ==========================================
# ГОЛОС
# ==========================================

def transcribe_voice(file_path: str) -> str:

    return "Голосовое сообщение"

# ==========================================
# ЧЕКИ
# ==========================================

def read_receipt_image(image_path: str) -> dict:

    return {
        "тип": "расход",
        "сумма": 0,
        "валюта": "RUB",
        "категория": "Прочее",
        "подкатегория": "",
        "магазин": "",
        "описание": "Чек",
        "уверенность": 0.0
    }

# ==========================================
# ВЫПИСКИ
# ==========================================

def parse_bank_statement(file_path: str) -> list:

    return [
        {
            "тип": "расход",
            "сумма": 0,
            "валюта": "RUB",
            "категория": "Прочее",
            "описание": "Банковская операция"
        }
    ]
