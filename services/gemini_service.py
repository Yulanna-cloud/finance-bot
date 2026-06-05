import os
import json
import logging
import re
import base64
import io
from groq import Groq

logger = logging.getLogger(__name__)

groq_api_key = os.getenv("GROQ_API_KEY")
groq_client = None
if groq_api_key:
    groq_client = Groq(api_key=groq_api_key)
else:
    logger.error("GROQ_API_KEY не найден!")

# Члены семьи — имя → полное имя
FAMILY_MEMBERS = {
    "маргарита": "Маргарита П.",
    "диана":     "Диана Ш.",
    "алексей":   "Алексей П.",
    "алёша":     "Алексей П.",
    "алеша":     "Алексей П.",
    "райса":     "Райса Г.",
    "юланна":    "Юланна Г.",
    "салават":   "Салават Г.",
    "дамир":     "Дамир Г.",
    "ольга":     "Ольга Г.",
}

CATEGORY_RULES = {
    "Продукты": [
        "пятерочка", "магнит", "находка", "ежик", "светофор",
        "монеточка", "перекресток", "лента", "вкусвилл",
        "самокат", "яндекс лавка", "spar", "fix price", "дикси", "окей"
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
    "Жилье":        ["жкх", "коммуналка", "аренда", "квартира"],
    "Коммуналка":   ["свет", "электричество", "интернет", "wifi", "газ"],
    "Медицина":     ["аптека", "врач", "клиника", "таблетки", "витамины", "36.6", "ригла"],
    "Животные":     ["корм", "ветеринар", "whiskas", "royal canin"],
    "Красота":      ["салон", "парикмахер", "маникюр", "тени", "тушь", "помада", "косметик"],
    "Бытовая химия":["порошок", "мыло", "шампунь", "гель", "фейри", "зубная", "туалетная бумага"],
    "Одежда":       ["одежда", "обувь", "куртка", "кроссовк", "wildberries", "ozon", "трусы", "носки"],
    "Развлечения":  ["кино", "театр", "концерт", "музей"],
    "Подписки":     ["яндекс плюс", "yandex plus", "netflix", "spotify", "icloud"],
    "Табак":        ["сигарет", "табак", "папирос", "вейп"],
    "Доход":        ["зарплата", "аванс", "оклад", "фриланс", "подработка"],
    "Переводы":     ["перевод", "сбп"],
    "Обучение":     ["курс", "обучение", "урок", "занятие", "секци", "кружок", "танц",
                     "репетитор", "школа", "садик", "детский сад"],
    "Дети":         ["детск", "игрушк", "lego", "подгузник"],
    "Электротовары":["гирлянда", "лампочк", "батарейк", "удлинитель", "розетк", "провод"],
}

GROCERY_STORES = [
    "пятерочка", "магнит", "находка", "ежик", "светофор",
    "монеточка", "перекресток", "лента", "вкусвилл",
    "самокат", "яндекс лавка", "spar", "fix price", "дикси", "окей"
]


def extract_family_member(text: str) -> str:
    """Ищет имя члена семьи в тексте, возвращает полное имя или ''."""
    t = text.lower()
    for key, full_name in FAMILY_MEMBERS.items():
        if key in t:
            return full_name
    return ""


def parse_caption_instruction(caption: str) -> dict:
    """
    Разбирает подпись к фото чека.
    'обучение Маргарите танцы 2400' →
    {категория: Обучение, подкатегория: Танцы, получатель: Маргарита П., сумма: 2400}
    """
    text_lower = caption.lower()
    result = {
        "использовать_подпись": True,
        "категория": "Прочее",
        "подкатегория": "",
        "описание": caption,
        "получатель": "",
        "магазин": "",
        "сумма": None,
    }

    # Ищем сумму
    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', caption)
    if amounts:
        try:
            result["сумма"] = float(amounts[-1].replace(" ", "").replace(",", "."))
        except ValueError:
            pass

    # Ищем члена семьи
    family = extract_family_member(caption)
    if family:
        result["получатель"] = family

    # Ищем категорию
    for cat, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw in text_lower:
                result["категория"] = cat
                break
        if result["категория"] != "Прочее":
            break

    # Подкатегория — если есть "танцы", "английский" и т.д.
    subcat_map = {
        "танц": "Танцы", "английск": "Английский", "математик": "Математика",
        "рисован": "Рисование", "музык": "Музыка", "спорт": "Спорт",
        "плавани": "Плавание", "футбол": "Футбол", "шахмат": "Шахматы",
    }
    for key, val in subcat_map.items():
        if key in text_lower:
            result["подкатегория"] = val
            break

    return result


def classify_text(text: str) -> dict:
    text_lower = text.lower()

    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', text)
    amounts = [float(a.replace(" ", "").replace(",", ".")) for a in amounts if a.strip()]
    main_amount = amounts[0] if amounts else 0.0

    # Тип операции
    income_words = ["зарплата", "аванс", "доход", "получил", "получила",
                    "пришло", "приход", "перевел", "перевела", "прислал", "прислала"]
    op_type = "доход" if any(w in text_lower for w in income_words) else "расход"

    # Член семьи
    family = extract_family_member(text)

    # Детальный ввод с магазином
    store_name = None
    for store in GROCERY_STORES:
        if store in text_lower:
            store_name = store
            break

    if store_name and len(amounts) > 1:
        return _classify_detailed(text, store_name, amounts, op_type)

    # Словарный поиск
    for category, keywords in CATEGORY_RULES.items():
        for keyword in keywords:
            if keyword in text_lower:
                subcat = get_subcat(category, text_lower)
                return {
                    "тип": op_type,
                    "сумма": main_amount,
                    "категория": category,
                    "подкатегория": subcat,
                    "магазин": keyword.title() if category in ("Продукты", "Кафе") else "",
                    "описание": text,
                    "получатель": family if op_type == "расход" else "",
                    "отправитель": family if op_type == "доход" else "",
                    "уверенность": 0.95
                }

    if not groq_client:
        return _default(text, main_amount, op_type)

    # Передаём в Groq с контекстом о семье
    family_hint = f'\nЧлен семьи упомянут: "{family}" — если расход, запиши в получатель; если доход — в отправитель.' if family else ""

    prompt = f"""Ты помощник для учёта финансов. Верни ТОЛЬКО JSON без markdown.

Операция: "{text}"
{family_hint}

Категории (выбери одну):
Продукты, Кафе, Транспорт, Жилье, Коммуналка, Медицина, Обучение, Дети,
Животные, Красота, Бытовая химия, Одежда, Развлечения, Подписки, Табак, Доход, Переводы, Прочее

Правила:
- танцы/секция/кружок/урок/репетитор → категория "Обучение"
- если упомянуто для кого (Маргарите, Диане) → запиши в "подкатегория" имя
- зарплата/аванс/пришло/приход → тип "доход"

{{
  "тип": "{op_type}",
  "сумма": {main_amount},
  "категория": "...",
  "подкатегория": "...",
  "магазин": "",
  "описание": "...",
  "получатель": "",
  "отправитель": "",
  "уверенность": 0.8
}}"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        if not result.get("сумма") and main_amount:
            result["сумма"] = main_amount
        # Добавляем семью если Groq не заполнил
        if family and op_type == "расход" and not result.get("получатель"):
            result["получатель"] = family
        if family and op_type == "доход" and not result.get("отправитель"):
            result["отправитель"] = family
        return result
    except Exception as e:
        logger.error(f"Ошибка Groq classify: {e}")
        return _default(text, main_amount, op_type)


def _classify_detailed(text: str, store_name: str, amounts: list, op_type: str) -> dict:
    if not groq_client:
        return _default(text, amounts[0] if amounts else 0, op_type)

    prompt = f"""Разбери покупку и верни ТОЛЬКО JSON массив без markdown.

Текст: "{text}"

Правила:
- Первая сумма — общий чек магазина
- Остальные суммы — отдельные товары
- Остаток (общая - перечисленные) → Продукты
- Если остаток <= 0 — не добавляй Продукты

Категории: Табак, Бытовая химия, Красота, Одежда, Электротовары, Продукты, Прочее

[{{"категория":"Табак","сумма":185,"описание":"сигареты"}}]"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        items = json.loads(raw)
        return {
            "тип": op_type,
            "магазин": store_name.title(),
            "мультизапись": True,
            "позиции": items
        }
    except Exception as e:
        logger.error(f"Ошибка детального разбора: {e}")
        return _default(text, amounts[0] if amounts else 0, op_type)


def get_subcat(category: str, text_lower: str) -> str:
    subcats = {
        "Продукты": {"молочка": ["молоко", "кефир", "йогурт", "сыр", "творог"],
                     "мясо": ["мясо", "курица", "говядина", "фарш"],
                     "хлеб": ["хлеб", "батон", "булка"]},
        "Кафе":     {"кофе": ["кофе", "капучино", "латте", "эспрессо"],
                     "фастфуд": ["бургер", "kfc", "вкусно", "шаверма"]},
        "Транспорт":{"бензин": ["бензин", "заправка", "лукойл", "роснефть"],
                     "такси": ["такси", "uber", "яндекс go"]},
        "Обучение": {"Танцы": ["танц"], "Английский": ["английск"],
                     "Математика": ["математик"], "Рисование": ["рисован"]},
    }
    if category in subcats:
        for subcat, words in subcats[category].items():
            if any(w in text_lower for w in words):
                return subcat
    return ""


def _default(text, amount, op_type):
    return {
        "тип": op_type, "сумма": amount, "категория": "Прочее",
        "подкатегория": "", "магазин": "", "описание": text,
        "получатель": "", "отправитель": "", "уверенность": 0.3
    }


def transcribe_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    if not groq_client:
        return ""
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "voice.ogg"
        transcription = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio_file,
            language="ru"
        )
        return transcription.text.strip()
    except Exception as e:
        logger.error(f"Ошибка голоса Groq: {e}")
        return ""


def read_receipt_image(image_bytes: bytes) -> dict:
    if not groq_client:
        return {"ошибка": "нет GROQ_API_KEY", "позиции": []}
    try:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # ====== ИСПРАВЛЕНО: промпт теперь требует торговое название, не юрлицо ======
        prompt = """Прочитай чек и верни ТОЛЬКО JSON без markdown.

КРИТИЧЕСКИ ВАЖНО для поля "магазин":
- Используй ТОРГОВОЕ название (логотип или бренд вверху чека), НЕ юридическое название
- Юридическое название (ООО, АО, ЗАО, ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ) в поле "магазин" ЗАПРЕЩЕНО
- Логотип всегда крупно вверху чека — это и есть правильное название
- Примеры правильных названий: Пятёрочка, Магнит, Лента, Перекрёсток, Дикси, ВкусВилл, Светофор
- Если торговое название не распознаётся — напиши короткое слово без ООО/АО/ЗАО

Соответствие юрлицо → торговое название:
- ООО АГРОТОРГ / ООО ВЫРУЧАЙ / содержит ПЯТЕРОЧКА → Пятёрочка
- ООО ТАНДЕР / содержит МАГНИТ → Магнит
- ООО ДИКСИ → Дикси
- ЗАО ТАНДЕР → Магнит
- АО ТАНДЕР → Магнит
- содержит ПЕРЕКРЕСТОК → Перекрёсток
- содержит ЛЕНТА → Лента

Также читай все позиции товаров из чека.

{
  "магазин": "торговое название магазина",
  "дата": "дата или null",
  "итого": общая_сумма_числом,
  "позиции": [
    {"название": "название товара", "сумма": цена_числом},
    {"название": "название товара", "сумма": цена_числом}
  ],
  "категории": [
    {"категория": "Продукты", "сумма": общая_сумма_числом}
  ]
}
Категории для поля категории: Продукты, Бытовая химия, Табак, Красота, Одежда, Прочее"""
        # ==========================================================================

        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt}
            ]}]
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
Категории: Продукты, Кафе, Транспорт, Жилье, Коммуналка, Медицина, Одежда, Развлечения, Подписки, Табак, Бытовая химия, Доход, Прочее
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
