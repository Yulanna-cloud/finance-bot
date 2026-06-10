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
    "маргарите": "Маргарита П.",
    "маргариты": "Маргарита П.",
    "рита":      "Маргарита П.",
    "рите":      "Маргарита П.",
    "риты":      "Маргарита П.",
    "диана":     "Диана Ш.",
    "диане":     "Диана Ш.",
    "дианы":     "Диана Ш.",
    "алексей":   "Алексей П.",
    "алексею":   "Алексей П.",
    "алёша":     "Алексей П.",
    "алеша":     "Алексей П.",
    "алёше":     "Алексей П.",
    "алеше":     "Алексей П.",
    "райса":     "Райса Г.",
    "райсе":     "Райса Г.",
    "юланна":    "Юланна Г.",
    "юланне":    "Юланна Г.",
    "салават":   "Салават Г.",
    "салавату":  "Салават Г.",
    "дамир":     "Дамир Г.",
    "дамиру":    "Дамир Г.",
    "ольга":     "Ольга Г.",
    "ольге":     "Ольга Г.",
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
    "Одежда":       ["одежда", "обувь", "куртка", "кроссовк", "wildberries", "вайлдберриз",
                     "вайлдберис", "ozon", "озон", "трусы", "носки"],
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

STORE_NAME_MAP = [
    (["агроторг", "пятерочка", "пятёрочка", "pyaterochka", "5ка", "5-ка"], "Пятёрочка"),
    (["тандер", "магнит", "magnit"], "Магнит"),
    (["дикси", "dixi"], "Дикси"),
    (["перекресток", "перекрёсток", "perekrestok"], "Перекрёсток"),
    (["лента", "lenta"], "Лента"),
    (["вкусвилл", "вкус вилл", "vkusvill"], "ВкусВилл"),
    (["окей", "o'key", "okey"], "О'Кей"),
    (["светофор"], "Светофор"),
    (["монеточка", "monetochka"], "Монеточка"),
    (["самокат", "samokat"], "Самокат"),
    (["метро", "metro cash"], "Метро"),
    (["ашан", "auchan"], "Ашан"),
    (["глобус"], "Глобус"),
    (["спар", "spar"], "Спар"),
    (["fix price", "фикс прайс", "фикспрайс"], "Fix Price"),
    (["красное белое", "красное & белое"], "Красное&Белое"),
    (["бристоль"], "Бристоль"),
]


def normalize_store_name(raw_name: str) -> str:
    if not raw_name:
        return ""
    name_lower = raw_name.lower().strip()
    cleaned = re.sub(
        r'\b(ооо|оао|зао|пао|ао|общество с ограниченной ответственностью|'
        r'акционерное общество|публичное акционерное общество)\b',
        '', name_lower
    )
    cleaned = re.sub(r'["""«»\']+', '', cleaned).strip()
    for keywords, trade_name in STORE_NAME_MAP:
        for kw in keywords:
            if kw in name_lower or kw in cleaned:
                return trade_name
    result = cleaned.strip().title()
    return result if result else raw_name


def extract_family_member(text: str) -> str:
    """Ищет имя члена семьи в тексте, возвращает полное имя или ''."""
    t = text.lower()
    for key, full_name in FAMILY_MEMBERS.items():
        if key in t:
            return full_name
    return ""


def parse_caption_instruction(caption: str) -> dict:
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
    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', caption)
    if amounts:
        try:
            result["сумма"] = float(amounts[-1].replace(" ", "").replace(",", "."))
        except ValueError:
            pass
    family = extract_family_member(caption)
    if family:
        result["получатель"] = family
    for cat, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw in text_lower:
                result["категория"] = cat
                break
        if result["категория"] != "Прочее":
            break
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


def is_multi_line_input(text: str) -> bool:
    """Определяет, содержит ли текст несколько покупок."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) >= 2:
        lines_with_amounts = sum(1 for l in lines if re.search(r'\d+', l))
        if lines_with_amounts >= 2:
            return True
    amounts = re.findall(r'(?<!\d)\d+(?:[.,]\d+)?(?!\d)', text)
    if len(amounts) >= 2:
        return True
    return False


def classify_text_multi(text: str) -> list:
    if not groq_client:
        return _split_lines_fallback(text)

    income_words = ["зарплата", "аванс", "доход", "получил", "получила",
                    "пришло", "приход", "перевел", "перевела", "прислал", "прислала"]
    op_type = "доход" if any(w in text.lower() for w in income_words) else "расход"
    family = extract_family_member(text)
    family_hint = f'\nЧлен семьи: "{family}"' if family else ""

    prompt = f"""Ты помощник для учёта финансов. Разбери текст на отдельные покупки.
Верни ТОЛЬКО JSON массив без markdown, каждый элемент — отдельная операция.

Текст: "{text}"{family_hint}

ВАЖНЫЕ ПРАВИЛА:
- Каждый товар/покупка — отдельный элемент массива
- Если в тексте упомянут магазин (wildberries, пятерочка и т.д.) — ставь его магазином У ВСЕХ товаров из этого списка
- wildberries/вайлдберриз/wb/вб → магазин "Wildberries"
- ozon/озон → магазин "Ozon"
- Продукты (фрукты, овощи, молоко и т.д.) → категория "Продукты"
- Если магазин один на весь список — у каждого товара одинаковый магазин
- зарплата/аванс/пришло → тип "доход", иначе "расход"

Категории: Продукты, Кафе, Транспорт, Жилье, Коммуналка, Медицина, Обучение, Дети,
Животные, Красота, Бытовая химия, Одежда, Развлечения, Подписки, Табак, Доход, Переводы, Прочее

Пример для "Вайлдберриз цветок рассада 500 Манго 500":
[
  {{"тип":"расход","сумма":500,"категория":"Одежда","подкатегория":"","магазин":"Wildberries","описание":"цветок рассада","получатель":"","отправитель":"","уверенность":0.9}},
  {{"тип":"расход","сумма":500,"категория":"Продукты","подкатегория":"","магазин":"Wildberries","описание":"манго","получатель":"","отправитель":"","уверенность":0.9}}
]"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        items = json.loads(raw)
        if not isinstance(items, list):
            items = [items]
        # Всегда дописываем семью после модели
        for item in items:
            if family:
                if item.get("тип") == "расход" and not item.get("получатель"):
                    item["получатель"] = family
                if item.get("тип") == "доход" and not item.get("отправитель"):
                    item["отправитель"] = family
        return items
    except Exception as e:
        logger.error(f"Ошибка classify_text_multi: {e}")
        return _split_lines_fallback(text)


def _split_lines_fallback(text: str) -> list:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 2:
        lines = [text]
    result = []
    for line in lines:
        op = classify_text(line)
        if op and op.get("сумма"):
            result.append(op)
    return result if result else [classify_text(text)]


def classify_text(text: str) -> dict:
    text_lower = text.lower()

    amounts = re.findall(r'\d[\d\s]*(?:[.,]\d+)?', text)
    amounts = [float(a.replace(" ", "").replace(",", ".")) for a in amounts if a.strip()]
    main_amount = amounts[0] if amounts else 0.0

    income_words = ["зарплата", "аванс", "доход", "получил", "получила",
                    "пришло", "приход", "перевел", "перевела", "прислал", "прислала"]
    op_type = "доход" if any(w in text_lower for w in income_words) else "расход"

    # Ищем члена семьи — ДО любых других проверок
    family = extract_family_member(text)

    store_name = None
    for store in GROCERY_STORES:
        if store in text_lower:
            store_name = store
            break

    if store_name and len(amounts) > 1:
        return _classify_detailed(text, store_name, amounts, op_type)

    # Словарный поиск категории
    for category, keywords in CATEGORY_RULES.items():
        for keyword in keywords:
            if keyword in text_lower:
                subcat = get_subcat(category, text_lower)
                if category == "Одежда" and keyword in ("wildberries", "вайлдберриз", "вайлдберис"):
                    store_display = "Wildberries"
                elif category == "Одежда" and keyword in ("ozon", "озон"):
                    store_display = "Ozon"
                elif category in ("Продукты", "Кафе"):
                    store_display = keyword.title()
                else:
                    store_display = ""
                return {
                    "тип": op_type,
                    "сумма": main_amount,
                    "категория": category,
                    "подкатегория": subcat,
                    "магазин": store_display,
                    "описание": text,
                    # ====== ИСПРАВЛЕНО: семья заполняется всегда, даже при словарном поиске ======
                    "получатель": family if op_type == "расход" else "",
                    "отправитель": family if op_type == "доход" else "",
                    # ==============================================================================
                    "уверенность": 0.95
                }

    # Если словарь не нашёл — идём в Groq
    if not groq_client:
        return _default(text, main_amount, op_type, family, op_type)

    family_hint = f'\nЧлен семьи упомянут: "{family}" — если расход, запиши в получатель; если доход — в отправитель.' if family else ""

    prompt = f"""Ты помощник для учёта финансов. Верни ТОЛЬКО JSON без markdown.

Операция: "{text}"
{family_hint}

Категории (выбери одну):
Продукты, Кафе, Транспорт, Жилье, Коммуналка, Медицина, Обучение, Дети,
Животные, Красота, Бытовая химия, Одежда, Развлечения, Подписки, Табак, Доход, Переводы, Прочее

Правила:
- wildberries/вайлдберриз → категория "Одежда", магазин "Wildberries"
- ozon/озон → категория "Одежда", магазин "Ozon"
- танцы/секция/кружок/урок/репетитор → категория "Обучение"
- зарплата/аванс/пришло/приход → тип "доход"
- если упомянут получатель (Маргарите, Рите, Диане и т.д.) → заполни поле "получатель"

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
        # Всегда дописываем семью — даже если Groq не заполнил
        if family and op_type == "расход" and not result.get("получатель"):
            result["получатель"] = family
        if family and op_type == "доход" and not result.get("отправитель"):
            result["отправитель"] = family
        return result
    except Exception as e:
        logger.error(f"Ошибка Groq classify: {e}")
        return _default(text, main_amount, op_type, family, op_type)


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


def _default(text, amount, op_type, family="", family_op_type="расход"):
    return {
        "тип": op_type,
        "сумма": amount,
        "категория": "Прочее",
        "подкатегория": "",
        "магазин": "",
        "описание": text,
        "получатель": family if family and family_op_type == "расход" else "",
        "отправитель": family if family and family_op_type == "доход" else "",
        "уверенность": 0.3
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
        prompt = """Прочитай чек и верни ТОЛЬКО JSON без markdown.

Найди торговое название магазина — логотип крупно вверху чека.
Юридическое название (ООО, АО, ЗАО) — НЕ использовать.

{
  "магазин": "торговое название с логотипа вверху чека",
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
Категории: Продукты, Бытовая химия, Табак, Красота, Одежда, Прочее"""

        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt}
            ]}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        if isinstance(result, dict) and result.get("магазин"):
            result["магазин"] = normalize_store_name(result["магазин"])
        return result
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
