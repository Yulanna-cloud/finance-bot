import os
import json
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

# Инициализация Gemini
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
else:
    logger.error("GEMINI_API_KEY не установлен в переменных окружения!")

def classify_text(text: str) -> dict:
    """Разбивает текст операции на составляющие с помощью Google Gemini."""
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

    # Полностью безопасный текст инструкции
    prompt = "Ты — эксперт по учету личных финансов. Твоя задача — разобрать текст сообщения и вернуть СТРОГО JSON-объект.\n\n" \
             "Выбирай категорию ТОЛЬКО из этого списка:\n" \
             "- Продукты (супермаркеты, еда, Пятерочка, Вкусвилл)\n" \
             "- Кафе (рестораны, кофе, фастфуд)\n" \
             "- Авто (бензин, заправка, запчасти, мойка)\n" \
             "- Транспорт (такси, метро, автобус)\n" \
             "- Жилье (коммуналка, аренда, ремонт)\n" \
             "- Развлечения (кино, хобби, книги)\n" \
             "- Здоровье (аптека, врачи)\n" \
             "- Одежда (обувь, вещи)\n" \
             "- Прочее (если не подходит ни один пункт)\n\n" \
             "Правила:\n" \
             "1. ТИП: 'расход' или 'доход'.\n" \
             "2. СУММА: Найди число в тексте и запиши как число. Если в тексте есть цифры, сумма не должна быть null.\n" \
             "3. ВАЛЮТА: 'RUB'.\n" \
             "4. МАГАЗИН: Название бренда с большой буквы (Пятерочка, Вкусвилл), если есть.\n" \
             "5. ОПИСАНИЕ: Краткая суть траты на русском.\n\n" \
             "Формат ответа — ТОЛЬКО чистый JSON, без разметки markdown (без знаков ```), без лишнего текста.\n\n" \
             "Разбери следующее сообщение пользователя:\n" + str(text)

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        
        raw = response.text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        result = json.loads(raw)
        
        allowed_categories = ["Продукты", "Кафе", "Авто", "Транспорт", "Жилье", "Развлечения", "Здоровье", "Одежда", "Прочее"]
        if result.get("категория") not in allowed_categories:
            result["категория"] = "Прочее"
            
        return result
        
    except Exception as e:
        logger.error(f"Ошибка в gemini_service: {e}")
        return default_response
