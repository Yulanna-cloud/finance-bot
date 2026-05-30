"""
Обработчик файлов банковских выписок.
Поддерживает CSV, Excel и PDF от Сбербанка.
"""

import logging
import io
import os
import json
import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import parse_bank_statement
from services.sheets_service import write_operations_batch

logger = logging.getLogger(__name__)

MAX_OPERATIONS = 200

# Продуктовые супермаркеты — пропускаем
SKIP_MERCHANTS = [
    "pyaterochka", "пятерочка", "magnit", "магнит", "krasnoe", "красное",
    "beloe", "белое", "находка", "ежик", "светофор", "монеточка",
    "perekrestok", "перекресток", "lenta", "лента", "вкусвилл", "spar",
    "дикси", "окей", "fix price", "самокат"
]

# Внутренние переброски между счетами — записываем как "между счетами"
INTERNAL_MARKERS = [
    "vklad-karta", "karta-vklad", "sberbank onl@in"
]

# Члены семьи — пишем в поле "получатель"
FAMILY_MEMBERS = [
    "маргарита", "диана ш", "диана александровна",
    "райса", "юланна", "салават", "дамир г", "ольга г",
    "алексей п", "алексей георгиевич",
]

# Нормализация имён — убираем отчества, оставляем "Имя Ф."
NAME_MAP = {
    "маргарита алексеевна": "Маргарита П.",
    "маргарита п":          "Маргарита П.",
    "диана александровна":  "Диана Ш.",
    "диана ш":              "Диана Ш.",
    "алексей георгиевич":   "Алексей П.",
    "алексей п":            "Алексей П.",
    "п. алексей":           "Алексей П.",
    "райса махмутовна":     "Райса Г.",
    "г. райса":             "Райса Г.",
    "райса г":              "Райса Г.",
    "ш. диана":             "Диана Ш.",
    "п. маргарита":         "Маргарита П.",
}


def normalize_name(name: str) -> str:
    """Нормализует имя — убирает отчество, приводит к короткому формату."""
    name_lower = name.lower().strip()
    for key, val in NAME_MAP.items():
        if key in name_lower:
            return val
    return name.strip()


def is_family(name: str) -> bool:
    """Проверяет, является ли получатель членом семьи."""
    name_lower = name.lower()
    return any(f in name_lower for f in FAMILY_MEMBERS)


def get_category(recv: str, op_type: str) -> str:
    """Определяет категорию по получателю и типу операции."""
    recv_lower = recv.lower()
    if "маргарита" in recv_lower or "диана" in recv_lower:
        return "Дети" if op_type == "расход" else "Доход"
    if "алексей" in recv_lower:
        return "Переводы" if op_type == "расход" else "Доход"
    if any(x in recv_lower for x in ["райса", "салават", "юланна", "дамир", "ольга"]):
        return "Семья"
    return "Доход" if op_type == "доход" else "Переводы"


def parse_sber_pdf(pdf_bytes: bytes) -> list:
    """Парсит PDF выписку Сбербанка через Groq."""
    try:
        import pypdf
        from services.gemini_service import groq_client

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"

        logger.info(f"PDF прочитан, длина текста: {len(full_text)}")

        if not groq_client:
            logger.error("GROQ_API_KEY не найден")
            return []

        prompt = f"""Ты разбираешь банковскую выписку Сбербанка. Верни ТОЛЬКО JSON массив без markdown.
Включай ТОЛЬКО операции которые реально есть в тексте выписки. Не придумывай операции.

ПРАВИЛО 1 — Пропусти полностью:
- Строки с "Нэсп Без Идентификации" или "Без Идентификации"
- Строки с PYATEROCHKA, KRASNOE, BELOE, MAGNIT, LENTA, PEREKRESTOK, DIXI, SPAR

ПРАВИЛО 2 — Переброски между счетами (SBERBANK ONL@IN, VKLAD-KARTA, KARTA-VKLAD):
- НЕ пропускай их, но ставь тип "между счетами", категория="Между счетами", магазин="", получатель=""
- Это не доход и не расход, просто перемещение денег между своими счетами

ПРАВИЛО 3 — Тип операции для всего остального:
- Сумма со знаком + = тип "доход"
- Сумма без знака или с минусом = тип "расход"
- ATM, банкомат, выдача наличных = тип "наличные"

ПРАВИЛО 4 — Члены семьи (пиши только имя и первую букву фамилии в поле "получатель"):
Маргарита П., Диана Ш., Алексей П., Райса Г., Юланна Г., Салават Г., Дамир Г., Ольга Г.
- Пример: "Маргарита Алексеевна" → получатель="Маргарита П."
- Пример: "Алексей Георгиевич" → получатель="Алексей П."
- Пример: "Диана Александровна" → получатель="Диана Ш."
- Пример: "Райса Махмутовна" → получатель="Райса Г."

ПРАВИЛО 5 — Все остальные люди в переводах (не семья) — частники:
- Пиши сокращённо в поле "магазин", поле "получатель" = ""
- Пример: "В. Динара Фаниловна" → магазин="Динара Ф."

ПРАВИЛО 6 — Магазины и кафе:
- поле "магазин" = название без города и RUS
- "BUTIK LILIYA STERLITAMAK RUS" → магазин="Butik Liliya"
- "IP EFIMOVA STERLITAMAK RUS" → магазин="IP Efimova"
- "MIRA STERLITAMAK RUS" → магазин="Mira"
- "MAGAZIN 1 STERLITAMAK RUS" → магазин="Magazin 1"

ПРАВИЛО 7 — Яндекс:
- Яндекс + ПРИХОД → тип="доход", категория="Доход", получатель="Алексей П.", магазин=""
- Яндекс + РАСХОД → тип="расход", категория="Подписки", магазин="Яндекс", получатель=""

Категории: Кафе, Транспорт, Одежда, Дом, Медицина, Коммуналка, Переводы, Дети, Семья, Подписки, Доход, Наличные, Между счетами, Прочее

Формат объекта:
{{"дата":"DD.MM.YYYY","сумма":число,"тип":"расход/доход/наличные/между счетами","категория":"...","магазин":"...","описание":"","получатель":"..."}}

Текст выписки:
{full_text[:6000]}"""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        logger.info(f"Groq ответ (первые 500 символов): {raw[:500]}")

        items = json.loads(raw)
        if not isinstance(items, list):
            logger.error(f"Groq вернул не массив: {type(items)}")
            return []

        result = []
        for item in items:
            try:
                amount = float(str(item.get("сумма", 0)).replace(",", "."))
            except (ValueError, TypeError):
                continue
            if amount <= 0:
                continue

            op_type = item.get("тип", "расход")
            магазин_raw = item.get("магазин", "")
            получатель_raw = item.get("получатель", "")
            описание = item.get("описание", "").lower()
            проверка = f"{описание} {магазин_raw.lower()} {получатель_raw.lower()}"

            # Фильтр продуктовых магазинов
            if any(s in проверка for s in SKIP_MERCHANTS):
                logger.info(f"Пропускаем супермаркет: {магазин_raw}")
                continue

            # Проверка на внутренние переброски (на случай если Groq не распознал)
            if any(s in проверка for s in INTERNAL_MARKERS):
                op_type = "между счетами"
                item["категория"] = "Между счетами"
                магазин_raw = ""
                получатель_raw = ""

            # Нормализация имён получателя
            recv = normalize_name(получатель_raw) if получатель_raw else ""
            shop = магазин_raw

            # Если получатель — не семья, переносим в магазин
            if recv and not is_family(recv):
                shop = recv
                recv = ""

            # Категория по получателю (если не задана явно или задана неверно)
            category = item.get("категория", "Прочее")
            if recv and category not in ("Дети", "Семья", "Доход", "Переводы"):
                category = get_category(recv, op_type)

            result.append({
                "дата": item.get("дата", ""),
                "сумма": amount,
                "тип": op_type,
                "категория": category,
                "подкатегория": "",
                "магазин": shop,
                "описание": item.get("описание", ""),
                "получатель": recv,
                "уверенность": 0.9
            })

        logger.info(f"Разобрано операций: {len(result)}")
        return result

    except ImportError:
        logger.error("pypdf не установлен")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от Groq: {e}")
        return []
    except Exception as e:
        logger.error(f"Ошибка парсинга PDF: {e}", exc_info=True)
        return []


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or ""
    ext = os.path.splitext(filename)[1].lower()

    if ext not in (".csv", ".xlsx", ".xls", ".pdf"):
        await update.message.reply_text(
            "📄 Поддерживаю файлы *CSV*, *Excel* (.xlsx, .xls) и *PDF* (выписка Сбербанка)",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"📊 Читаю выписку *{filename}*...", parse_mode="Markdown")

    try:
        file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        file_bytes = buf.read()

        operations = []

        if ext == ".pdf":
            await update.message.reply_text("📄 Определила формат Сбербанка. Анализирую операции...")
            operations = parse_sber_pdf(file_bytes)
        else:
            buf = io.BytesIO(file_bytes)
            df = read_statement_file(buf, ext, filename)

            if df is None or df.empty:
                await update.message.reply_text("❌ Не смогла прочитать файл.")
                return

            if len(df) > MAX_OPERATIONS:
                df = df.head(MAX_OPERATIONS)

            text_repr = df.to_string(index=False, max_rows=MAX_OPERATIONS)
            operations = parse_bank_statement(text_repr)

        if not operations:
            await update.message.reply_text(
                "❌ Не нашла операций для записи.\n"
                "Возможно все операции — это супермаркеты (они пропускаются) "
                "или внутренние переброски между счетами."
            )
            return

        total_expense = sum(op.get("сумма", 0) for op in operations if op.get("тип") == "расход")
        total_income = sum(op.get("сумма", 0) for op in operations if op.get("тип") == "доход")
        internal_count = sum(1 for op in operations if op.get("тип") == "между счетами")
        cash_count = sum(1 for op in operations if op.get("тип") == "наличные")

        preview_lines = ["📋 *Найдены операции:*\n"]
        for op in operations[:10]:
            тип = op.get("тип", "")
            emoji = "💰" if тип == "доход" else "🔄" if тип == "между счетами" else "🏧" if тип == "наличные" else "💸"
            получатель = op.get("получатель", "")
            магазин = op.get("магазин", "")
            описание = op.get("описание", "")
            показать = получатель or магазин or описание or "—"
            preview_lines.append(
                f"{emoji} {показать[:40]} — "
                f"{op.get('сумма', 0):,.0f} ₽ ({op.get('категория', '')})"
            )
        if len(operations) > 10:
            preview_lines.append(f"_...и ещё {len(operations) - 10} операций_")

        await update.message.reply_text("\n".join(preview_lines), parse_mode="Markdown")

        ok, errors = write_operations_batch(operations, source="выписка_сбер")

        msg = (
            f"✅ Выписка обработана!\n\n"
            f"📥 Записано: *{ok}* операций\n"
            f"💸 Расходы: *{total_expense:,.0f} ₽*\n"
            f"💰 Доходы: *{total_income:,.0f} ₽*"
        )
        if internal_count:
            msg += f"\n🔄 Переброски между счетами: {internal_count} (не считаются)"
        if cash_count:
            msg += f"\n🏧 Снятие наличных: {cash_count} раз (не считается)"
        if errors:
            msg += f"\n⚠️ Не записалось: {errors}"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка handle_file: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при обработке файла.")


def read_statement_file(buf: io.BytesIO, ext: str, filename: str) -> pd.DataFrame:
    try:
        if ext in (".xlsx", ".xls"):
            engine = "openpyxl" if ext == ".xlsx" else "xlrd"
            for header_row in [0, 1, 2]:
                try:
                    df = pd.read_excel(buf, engine=engine, header=header_row)
                    buf.seek(0)
                    if len(df) > 0 and len(df.columns) > 2:
                        return df
                except Exception:
                    buf.seek(0)
                    continue
        else:
            for encoding in ["utf-8", "cp1251", "utf-8-sig"]:
                for sep in [";", ",", "\t"]:
                    try:
                        buf.seek(0)
                        df = pd.read_csv(buf, encoding=encoding, sep=sep, on_bad_lines="skip")
                        if len(df) > 0 and len(df.columns) > 2:
                            return df
                    except Exception:
                        continue
    except Exception as e:
        logger.error(f"Ошибка чтения файла: {e}")
    return None
