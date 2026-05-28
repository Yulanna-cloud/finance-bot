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
    date = str(row.get("дата", ""))
    category_raw = str(row.get("категория", "")).lower()
    description = str(row.get("описание", ""))
    amount_raw = str(row.get("сумма", "0")).replace(" ", "").replace(",", ".")
    is_income = row.get("тип") == "доход"

    try:
        amount = abs(float(amount_raw))
    except ValueError:
        return None

    if amount <= 0:
        return None

    desc_lower = description.lower()

    # Пропускаем внутренние переброски
    if any(s in desc_lower for s in ["vklad-karta", "karta-vklad", "sberbank onl@in"]):
        return None

    # Пропускаем супермаркеты
    if "супермаркет" in category_raw or any(s in desc_lower for s in SKIP_MERCHANTS):
        return None

    # Наличные
    if "наличн" in category_raw or "atm" in desc_lower:
        return {
            "дата": date,
            "сумма": amount,
            "тип": "наличные",
            "категория": "Наличные",
            "подкатегория": "",
            "магазин": "",
            "описание": "Снятие наличных",
            "получатель": "",
            "уверенность": 1.0
        }

    # Определяем получателя/отправителя из описания
    получатель = ""
    our_category = None
    op_type = "доход" if is_income else "расход"

    for name, cat in PEOPLE_CATEGORIES.items():
        if name in desc_lower:
            our_category = cat
            # Извлекаем полное имя из описания
            # "Перевод для П. Маргарита Алексеевна" → "Маргарита П."
            name_match = re.search(r'(?:для|от)\s+([А-ЯЁ]\.\s+[А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)?)', description)
            if name_match:
                получатель = name_match.group(1).strip()
            break

    if not our_category:
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
            if key in category_raw or key in desc_lower:
                our_category = val
                break

    if not our_category:
        our_category = "Доход" if is_income else "Прочее"

    return {
        "дата": date,
        "сумма": amount,
        "тип": op_type,
        "категория": our_category,
        "подкатегория": "",
        "магазин": "",
        "описание": description or category_raw,
        "получатель": получатель,
        "уверенность": 0.9
    }


def parse_sber_pdf(pdf_bytes: bytes) -> list:
    """Парсит PDF выписку Сбербанка через Groq."""
    try:
        import pypdf
        from services.gemini_service import groq_client

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"

        if not groq_client:
            logger.error("GROQ_API_KEY не найден")
            return []

        prompt = f"""Ты разбираешь банковскую выписку Сбербанка. Верни ТОЛЬКО JSON массив без markdown.

Правила:
1. ПРОПУСТИ операции где в описании есть: "SBERBANK ONL@IN", "VKLAD-KARTA", "KARTA-VKLAD" — это внутренние переброски
2. ПРОПУСТИ супермаркеты: Пятерочка, Магнит, KRASNOE, BELOE, MAGAZIN и подобные — они вводятся вручную
3. Суммы со знаком + это доходы, без знака или с - это расходы
4. Снятие наличных (ATM, банкомат) — тип "наличные"

Для каждой операции верни:
{{
  "дата": "DD.MM.YYYY",
  "сумма": число (всегда положительное),
  "тип": "расход" или "доход" или "наличные",
  "категория": одна из: Продукты/Кафе/Транспорт/Одежда/Дом/Медицина/Коммуналка/Переводы/Дети/Подписки/Доход/Наличные/Прочее,
  "описание": краткое описание операции,
  "получатель": имя человека если это перевод (например "Маргарита П."), иначе ""
}}

Категории для переводов людям:
- Маргарита П. → Дети
- Диана Ш. → Дети  
- Алексей П. → Переводы
- Раиса Г. → Семья
- Любой другой перевод человеку → Переводы

Текст выписки:
{full_text[:6000]}"""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        items = json.loads(raw)

        # Фильтруем и нормализуем
        result = []
        for item in items:
            amount = float(item.get("сумма", 0))
            if amount <= 0:
                continue
            result.append({
                "дата": item.get("дата", ""),
                "сумма": amount,
                "тип": item.get("тип", "расход"),
                "категория": item.get("категория", "Прочее"),
                "подкатегория": "",
                "магазин": "",
                "описание": item.get("описание", ""),
                "получатель": item.get("получатель", ""),
                "уверенность": 0.9
            })
        return result

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
