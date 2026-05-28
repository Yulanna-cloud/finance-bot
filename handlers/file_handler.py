"""
Обработчик файлов банковских выписок.
Поддерживает CSV, Excel и PDF от Сбербанка.
"""

import logging
import io
import os
import re
import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import parse_bank_statement
from services.sheets_service import write_operations_batch

logger = logging.getLogger(__name__)

MAX_OPERATIONS = 200

# Магазины которые пропускаем (вводятся вручную)
SKIP_MERCHANTS = [
    "pyaterochka", "пятерочка", "magnit", "магнит", "krasnoe", "красное",
    "белое", "magazin", "находка", "ежик", "светофор", "монеточка",
    "перекресток", "лента", "вкусвилл", "spar", "дикси", "окей",
    "fix price", "самокат"
]

# Внутренние переброски Сбера — пропускаем
SKIP_DESCRIPTIONS = [
    "vklad-karta", "karta-vklad", "sberbank onl@in"
]

# Имена людей → категории
PEOPLE_CATEGORIES = {
    "маргарита": "Дети",
    "диана ш": "Дети",
    "алексей п": "Переводы",
    "раиса": "Семья",
}


def should_skip(description: str, category: str) -> bool:
    """Проверяет нужно ли пропустить операцию."""
    desc_lower = description.lower()

    # Пропускаем внутренние переброски
    for skip in SKIP_DESCRIPTIONS:
        if skip in desc_lower:
            return True

    # Пропускаем супермаркеты
    if "супермаркет" in category.lower():
        for merchant in SKIP_MERCHANTS:
            if merchant in desc_lower:
                return True

    return False


def classify_sber_operation(row: dict) -> dict | None:
    """
    Классифицирует одну операцию из выписки Сбербанка.
    Возвращает None если операцию нужно пропустить.
    """
    date = str(row.get("дата", ""))
    category_raw = str(row.get("категория", "")).lower()
    description = str(row.get("описание", ""))
    amount_raw = str(row.get("сумма", "0")).replace(" ", "").replace(",", ".")

    try:
        amount = abs(float(amount_raw))
    except ValueError:
        return None

    if amount <= 0:
        return None

    # Определяем тип операции
    is_income = "+" in str(row.get("сумма_raw", "")) or row.get("тип") == "доход"

    if should_skip(description, category_raw):
        return None

    desc_lower = description.lower()

    # Наличные — записываем отдельно, не считаем
    if "наличн" in category_raw or "atm" in desc_lower or "банкомат" in desc_lower:
        return {
            "дата": date,
            "сумма": amount,
            "тип": "наличные",
            "категория": "Наличные",
            "подкатегория": "",
            "магазин": "",
            "описание": "Снятие наличных",
            "уверенность": 1.0
        }

    # Определяем категорию по имени человека
    for name, cat in PEOPLE_CATEGORIES.items():
        if name in desc_lower:
            op_type = "доход" if is_income else "расход"
            return {
                "дата": date,
                "сумма": amount,
                "тип": op_type,
                "категория": cat,
                "подкатегория": "",
                "магазин": "",
                "описание": description,
                "уверенность": 0.95
            }

    # Доходы
    if is_income:
        return {
            "дата": date,
            "сумма": amount,
            "тип": "доход",
            "категория": "Доход",
            "подкатегория": "",
            "магазин": "",
            "описание": description,
            "уверенность": 0.85
        }

    # Категории Сбербанка → наши категории
    category_map = {
        "рестораны": "Кафе",
        "кафе": "Кафе",
        "одежда": "Одежда",
        "аксессуары": "Одежда",
        "дома": "Дом",
        "транспорт": "Транспорт",
        "медицин": "Медицина",
        "аптек": "Медицина",
        "связь": "Коммуналка",
        "интернет": "Коммуналка",
        "жкх": "Коммуналка",
        "коммунал": "Коммуналка",
        "перевод": "Переводы",
    }

    our_category = "Прочее"
    for key, val in category_map.items():
        if key in category_raw or key in desc_lower:
            our_category = val
            break

    return {
        "дата": date,
        "сумма": amount,
        "тип": "расход",
        "категория": our_category,
        "подкатегория": "",
        "магазин": "",
        "описание": description,
        "уверенность": 0.85
    }


def parse_sber_pdf(pdf_bytes: bytes) -> list:
    """Парсит PDF выписку Сбербанка."""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"

        operations = []
        lines = [l.strip() for l in full_text.split("\n") if l.strip()]

        i = 0
        while i < len(lines):
            line = lines[i]

            # Ищем строку с датой и временем: "28.05.2026 06:49 Категория сумма остаток"
            match = re.match(
                r'^(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})\s+(.+?)\s+([\d\s]+[,]\d{2})\s+([\d\s]+[,]\d{2})\s*$',
                line
            )
            if match:
                date = match.group(1)
                time_str = match.group(2)
                category_raw = match.group(3).strip()
                amount_str = match.group(4).replace(" ", "").replace(",", ".")
                # Остаток игнорируем

                # Знак + определяем из категории
                is_income = "+" in amount_str or any(
                    w in category_raw.lower() for w in ["перевод сбп", "перевод от"]
                )

                # Описание — следующая строка (код авторизации + описание)
                description = ""
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    # Пропускаем строку с кодом авторизации (6 цифр в начале)
                    if re.match(r'^\d{6}\s+', next_line):
                        description = re.sub(r'^\d{6}\s+', '', next_line).strip()
                        # Убираем "Операция по счету ****XXXX"
                        description = re.sub(r'\s*Операция по (счету|карте)\s+\*+\d+', '', description).strip()
                        i += 1

                try:
                    amount = float(amount_str)
                except ValueError:
                    i += 1
                    continue

                # Определяем тип по категории и описанию
                cat_lower = category_raw.lower()
                desc_lower = description.lower()

                # Пропускаем внутренние переброски
                if any(s in desc_lower for s in ["vklad-karta", "karta-vklad", "sberbank onl@in"]):
                    i += 1
                    continue

                # Пропускаем супермаркеты
                is_supermarket = "супермаркет" in cat_lower or any(
                    s in desc_lower for s in SKIP_MERCHANTS
                )
                if is_supermarket:
                    i += 1
                    continue

                # Определяем тип операции
                is_income = "+" in line or any(
                    w in desc_lower for w in ["перевод от ", "пополнение"]
                ) or any(
                    w in cat_lower for w in ["перевод сбп"]
                ) and "перевод от" in desc_lower

                # Наличные
                if "наличн" in cat_lower or "atm" in desc_lower:
                    operations.append({
                        "дата": date,
                        "сумма": amount,
                        "тип": "наличные",
                        "категория": "Наличные",
                        "подкатегория": "",
                        "магазин": "",
                        "описание": "Снятие наличных",
                        "уверенность": 1.0
                    })
                    i += 1
                    continue

                # Категория по имени человека
                our_category = None
                op_type = "доход" if is_income else "расход"

                for name, cat in PEOPLE_CATEGORIES.items():
                    if name in desc_lower:
                        our_category = cat
                        break

                if not our_category:
                    # Категория по типу из выписки
                    category_map = {
                        "рестораны": "Кафе",
                        "кафе": "Кафе",
                        "одежда": "Одежда",
                        "аксессуары": "Одежда",
                        "дома": "Дом",
                        "транспорт": "Транспорт",
                        "медицин": "Медицина",
                        "аптек": "Медицина",
                        "связь": "Коммуналка",
                        "интернет": "Коммуналка",
                        "жкх": "Коммуналка",
                        "коммунал": "Коммуналка",
                        "перевод": "Переводы",
                        "яндекс": "Подписки",
                    }
                    for key, val in category_map.items():
                        if key in cat_lower or key in desc_lower:
                            our_category = val
                            break

                if not our_category:
                    our_category = "Доход" if is_income else "Прочее"

                operations.append({
                    "дата": date,
                    "сумма": amount,
                    "тип": op_type,
                    "категория": our_category,
                    "подкатегория": "",
                    "магазин": "",
                    "описание": description or category_raw,
                    "уверенность": 0.9
                })

            i += 1

        return operations

    except ImportError:
        logger.error("pypdf не установлен")
        return []
    except Exception as e:
        logger.error(f"Ошибка парсинга PDF: {e}")
        return []


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or ""
    ext = os.path.splitext(filename)[1].lower()

    if ext not in (".csv", ".xlsx", ".xls", ".pdf"):
        await update.message.reply_text(
            "📄 Поддерживаю файлы *CSV*, *Excel* (.xlsx, .xls) и *PDF* (выписка Сбербанка)\n",
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
            await update.message.reply_text(
                "📄 Определила формат Сбербанка. Фильтрую операции...",
            )
            operations = parse_sber_pdf(file_bytes)

        else:
            buf = io.BytesIO(file_bytes)
            df = read_statement_file(buf, ext, filename)

            if df is None or df.empty:
                await update.message.reply_text(
                    "❌ Не смогла прочитать файл."
                )
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

        # Считаем статистику (наличные не считаем)
        total_expense = sum(
            op.get("сумма", 0) for op in operations
            if op.get("тип") == "расход"
        )
        total_income = sum(
            op.get("сумма", 0) for op in operations
            if op.get("тип") == "доход"
        )
        cash_count = sum(1 for op in operations if op.get("тип") == "наличные")

        # Показываем превью
        preview_lines = ["📋 *Найдены операции:*\n"]
        for op in operations[:10]:
            тип = op.get("тип", "")
            emoji = "💰" if тип == "доход" else "🏧" if тип == "наличные" else "💸"
            preview_lines.append(
                f"{emoji} {op.get('описание', '')[:35]} — "
                f"{op.get('сумма', 0):,.0f} ₽ ({op.get('категория', '')})"
            )
        if len(operations) > 10:
            preview_lines.append(f"_...и ещё {len(operations) - 10} операций_")

        await update.message.reply_text(
            "\n".join(preview_lines),
            parse_mode="Markdown"
        )

        ok, errors = write_operations_batch(operations, source="выписка_сбер")

        msg = (
            f"✅ Выписка обработана!\n\n"
            f"📥 Записано: *{ok}* операций\n"
            f"💸 Расходы: *{total_expense:,.0f} ₽*\n"
            f"💰 Доходы: *{total_income:,.0f} ₽*"
        )
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
