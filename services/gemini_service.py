import os
import json
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

# Gemini API
api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)
else:
    logger.error("GEMINI_API_KEY не найден!")

# =========================
# ВРЕМЕННАЯ ФУНКЦИЯ ДЛЯ ГОЛОСА
# =========================
def transcribe_voice(file_path: str) -> str:
    """
    Временная заглушка для голосовых сообщений.
    Пока просто возвращает текст.
    Позже подключим настоящее распознавание.
    """
    return "Голосовое сообщение"

# =========================
# КЛАССИФИКАЦИЯ ТЕКСТА
# =========================
def classify_text(text: str) -> dict:

    default_response = {
        "тип": "расход",
        "сумма": None,
        "валюта": "RUB",
        "категория": "Прочее",
        "подкатегория": "",
        "магазин": "",
        "описание": text,
        "уверенность": 0.0
    }

    if not api_key:
        return default_response

    prompt = (
        "Ты — эксперт по учету личных финансов.\n"
        "Верни только JSON.\n\n"

        "Категории:\n"
        "- Продукты\n"
        "- Кафе\n"
        "- Авто\n"
        "- Транспорт\n"
        "- Жилье\n"
        "- Развлечения\n"
        "- Здоровье\n"
        "- Одежда\n"
        "- Прочее\n\n"

        "Формат:\n"
        "{\n"
        '  "тип": "расход",\n'
        '  "сумма": 300,\n'
        '  "валюта": "RUB",\n'
        '  "категория": "Кафе",\n'
        '  "подкатегория": "",\n'
        '  "магазин": "",\n'
        '  "описание": "Кофе",\n'
        '  "уверенность": 0.95\n'
        "}\n\n"

        f"Сообщение пользователя: {text}"
    )

    try:

        model = genai.GenerativeModel("gemini-1.5-flash")

        response = model.generate_content(prompt)

        if not response.text:
            raise Exception("Пустой ответ Gemini")

        raw = response.text.strip()

        raw = raw.replace("```json", "")
        raw = raw.replace("```", "")
        raw = raw.strip()

        logger.info(f"GEMINI RAW: {raw}")

        result = json.loads(raw)

        allowed_categories = [
            "Продукты",
            "Кафе",
            "Авто",
            "Транспорт",
            "Жилье",
            "Развлечения",
            "Здоровье",
            "Одежда",
            "Прочее"
        ]

        if result.get("категория") not in allowed_categories:
            result["категория"] = "Прочее"

        return result

    except Exception as e:

        logger.error(f"Ошибка Gemini: {e}")

        return default_response
