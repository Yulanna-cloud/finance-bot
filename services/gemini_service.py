"""
Сервис для работы с Gemini API.
Отвечает за:
- Классификацию текстовых операций
- Расшифровку голоса (через Telegram + Gemini)
- Чтение фото чеков
- Парсинг банковских выписок
"""

import os
import json
import base64
import logging
import google.generativeai as genai
from typing import Optional

logger = logging.getLogger(__name__)

# Категории из твоей таблицы
CATEGORIES = [
    "Продукты", "Кафе", "Дети", "Жилье", "Коммуналка",
    "Транспорт", "Животные", "Медицина", "Красота", "Одежда",
    "Развлечения", "Подписки", "Доход", "Переводы", "Наличные", "Прочее"
]

SUBCATEGORIES = {
    "Продукты": ["молочка", "мясо", "овощи и фрукты", "хлеб", "бытовая химия"],
    "Кафе": ["кофе", "рестораны", "фастфуд"],
    "Дети": ["кружки", "одежда", "игрушки"],
    "Транспорт": ["такси", "метро", "бензин"],
    "Животные": ["корм", "ветеринар"],
    "Медицина": ["лекарства", "врач"],
    "Жилье": ["аренда"],
    "Коммуналка": ["свет", "интернет"],
    "Подписки": ["стриминг", "сервисы"],
    "Одежда": ["обувь", "верх"],
    "Доход": ["зарплата", "фриланс"],
}

STORE_RULES = {
    "пятерочка": ("Продукты", None, "Пятерочка"),
    "магнит": ("Продукты", None, "Магнит"),
    "перекресток": ("Продукты", None, "Перекресток"),
    "вкусвилл": ("Продукты", None, "ВкусВилл"),
    "ашан": ("Продукты", None, "Ашан"),
    "лента": ("Продукты", None, "Лента"),
    "вкусно и точка": ("Кафе", "фастфуд", "Вкусно и точка"),
    "kfc": ("Кафе", "фастфуд", "KFC"),
    "burger king": ("Кафе", "фастфуд", "Burger King"),
    "starbucks": ("Кафе", "кофе", "Starbucks"),
    "surf coffee": ("Кафе", "кофе", "Surf Coffee"),
    "yandex go": ("Транспорт", "такси", "Yandex Go"),
    "яндекс го": ("Транспорт", "такси", "Yandex Go"),
    "uber": ("Транспорт", "такси", "Uber"),
    "лукойл": ("Транспорт", "бензин", "Лукойл"),
    "роснефть": ("Транспорт", "бензин", "Роснефть"),
    "аптека 36.6": ("Медицина", "лекарства", "Аптека 36.6"),
    "ригла": ("Медицина", "лекарства", "Ригла"),
    "yandex plus": ("Подписки", "стриминг", "Yandex Plus"),
    "яндекс плюс": ("Подписки", "стриминг", "Yandex Plus"),
    "netflix": ("Подписки", "стриминг", "Netflix"),
    "spotify": ("Подписки", "стриминг", "Spotify"),
}


def init_gemini():
    """Инициализация Gemini API"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Не задан GEMINI_API_KEY!")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")


def quick_classify(text: str) -> Optional[dict]:
    """
    Быстрая классификация по правилам без вызова AI.
    Экономит лимиты Gemini для простых случаев.
    """
    text_lower = text.lower()
    for keyword, (cat, subcat, store) in STORE_RULES.items():
        if keyword in text_lower:
            return {"категория": cat, "подкатегория": subcat, "магазин": store}
    return None


def classify_text(text: str) -> dict:
    """
    Классифицирует текстовую операцию через Gemini.
    Возвращает dict с полями: сумма, категория, подкатегория, магазин, описание, тип, уверенность
    """
    # Сначала пробуем быстрые правила
    quick = quick_classify(text)

    model = init_gemini()

    cats_str = ", ".join(CATEGORIES)
    subcats_str = json.dumps(SUBCATEGORIES, ensure_ascii=False)

    prompt = f"""Ты помощник для учёта личных финансов. Разбери операцию и верни ТОЛЬКО JSON без markdown.

Операция: "{text}"

Доступные категории: {cats_str}
Подкатегории: {subcats_str}

{"Подсказка из правил: категория=" + quick['категория'] + ", магазин=" + str(quick.get('магазин')) if quick else ""}

Верни JSON в формате:
{{
  "сумма": число или null,
  "тип": "расход" или "доход" или "перевод",
  "категория": "из списка выше",
  "подкатегория": "из списка или null",
  "магазин": "название магазина или null",
  "описание": "краткое описание",
  "уверенность": число от 0 до 1
}}

Правила:
- Если упомянут магазин из сети (Пятерочка, ВкусВилл и т.д.) — категория Продукты
- Кафе, кофейня, ресторан — категория Кафе
- Такси, метро — Транспорт
- Если сумма явно указана — извлеки её
- Если текст про зарплату/доход — тип=доход
"""

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        # Убираем markdown если Gemini всё же добавил
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return result
    except Exception as e:
        logger.error(f"Ошибка Gemini classify_text: {e}")
        # Возвращаем минимальный результат чтобы хоть что-то записать
        return {
            "сумма": None,
            "тип": "расход",
            "категория": "Прочее",
            "подкатегория": None,
            "магазин": None,
            "описание": text,
            "уверенность": 0.3
        }


def read_receipt_image(image_bytes: bytes) -> list[dict]:
    """
    Читает фото чека через Gemini Vision.
    Возвращает список операций (одна на позицию в чеке).
    """
    model = init_gemini()

    cats_str = ", ".join(CATEGORIES)
    subcats_str = json.dumps(SUBCATEGORIES, ensure_ascii=False)

    prompt = f"""Ты OCR-система для чеков. Прочитай чек на фото и верни ТОЛЬКО JSON без markdown.

Доступные категории: {cats_str}
Подкатегории: {subcats_str}

Верни JSON в формате:
{{
  "магазин": "название магазина",
  "дата": "дата из чека или null",
  "итого": общая сумма числом,
  "способ_оплаты": "карта или наличные",
  "позиции": [
    {{
      "название": "название товара",
      "сумма": сумма числом,
      "категория": "из списка",
      "подкатегория": "из списка или null"
    }}
  ]
}}

Важно:
- Каждую позицию определяй отдельно
- Молоко/кефир/сыр → Продукты, молочка
- Порошок/мыло/гель → Продукты, бытовая химия
- Хлеб/батон → Продукты, хлеб
- Мясо/курица → Продукты, мясо
- Если позиций много — группируй по категориям
"""

    try:
        image_part = {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(image_bytes).decode()
        }
        response = model.generate_content([prompt, image_part])
        raw = response.text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Ошибка Gemini read_receipt: {e}")
        return {"ошибка": str(e), "позиции": []}


def transcribe_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """
    Расшифровывает голосовое сообщение через Gemini.
    Возвращает текст.
    """
    model = init_gemini()

    prompt = """Расшифруй это голосовое сообщение. 
Это запись расходов для учёта финансов.
Верни ТОЛЬКО текст того что было сказано, без пояснений."""

    try:
        audio_part = {
            "mime_type": mime_type,
            "data": base64.b64encode(audio_bytes).decode()
        }
        response = model.generate_content([prompt, audio_part])
        return response.text.strip()
    except Exception as e:
        logger.error(f"Ошибка Gemini transcribe_voice: {e}")
        return ""


def parse_bank_statement(text: str) -> list[dict]:
    """
    Парсит текст банковской выписки и классифицирует операции.
    Поддерживает форматы Сбербанк и Т-Банк.
    """
    model = init_gemini()

    cats_str = ", ".join(CATEGORIES)

    prompt = f"""Ты парсер банковских выписок. Разбери выписку и верни ТОЛЬКО JSON без markdown.

Выписка:
{text[:4000]}

Доступные категории: {cats_str}

Верни JSON — массив операций:
[
  {{
    "дата": "дата в формате DD.MM.YYYY",
    "сумма": число (положительное),
    "тип": "расход" или "доход",
    "категория": "из списка",
    "подкатегория": null или строка,
    "магазин": "контрагент/магазин",
    "описание": "описание из выписки",
    "уверенность": число от 0 до 1
  }}
]

Правила:
- Списание = расход, зачисление = доход
- Перевод себе (между своими счетами) = тип "перевод"
- ЖКХ/коммуналка → Коммуналка
- АЗС/бензин → Транспорт
- Аптека → Медицина
- Супермаркет/продукты → Продукты
"""

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Ошибка Gemini parse_bank_statement: {e}")
        return []
