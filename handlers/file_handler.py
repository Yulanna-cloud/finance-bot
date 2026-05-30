"""
Обработчик файлов банковских выписок.
Поддерживает CSV, Excel и PDF от Сбербанка.
Парсинг PDF — собственный код без ИИ, чтобы правила всегда соблюдались точно.
"""

import logging
import io
import os
import re
import json
import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import parse_bank_statement
from services.sheets_service import write_operations_batch

logger = logging.getLogger(__name__)

MAX_OPERATIONS = 200

# ─── Продуктовые супермаркеты — пропускаем полностью ─────────────────────────
SKIP_MERCHANTS = [
    "pyaterochka", "krasnoe", "beloe", "magnit", "lenta",
    "perekrestok", "dixi", "spar", "пятерочка", "магнит",
    "перекресток", "лента", "дикси", "находка", "ежик",
    "светофор", "монеточка", "вкусвилл", "окей", "fix price", "самокат",
]

# ─── Члены семьи ──────────────────────────────────────────────────────────────
FAMILY_NAMES = {
    "маргарита":  "Маргарита П.",
    "диана":      "Диана Ш.",
    "алексей":    "Алексей П.",
    "райса":      "Райса Г.",
    "юланна":     "Юланна Г.",
    "салават":    "Салават Г.",
    "дамир":      "Дамир Г.",
    "ольга г":    "Ольга Г.",
}

def normalize_family_name(raw: str) -> str | None:
    """Если строка содержит имя члена семьи — вернуть нормализованное имя."""
    r = raw.lower()
    for key, val in FAMILY_NAMES.items():
        if key in r:
            return val
    return None

def family_category(name: str, op_type: str) -> str:
    n = name.lower()
    if "маргарита" in n or "диана" in n:
        return "Дети"      if op_type == "расход" else "Доход"
    if "алексей" in n:
        return "Переводы"  if op_type == "расход" else "Доход"
    return "Семья"


def parse_sber_pdf(pdf_bytes: bytes) -> list:
    """
    Собственный парсер PDF-выписки Сбербанка.
    Не использует ИИ — читает строки напрямую и применяет правила в коде.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"
    except ImportError:
        logger.error("pypdf не установлен")
        return []
    except Exception as e:
        logger.error(f"Ошибка чтения PDF: {e}")
        return []

    logger.info(f"PDF прочитан, символов: {len(full_text)}")

    # Разбиваем на блоки операций.
    # Каждый блок начинается со строки вида "26.05.2026 13:35 Категория сумма"
    # Следующая строка — описание операции
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    # Паттерн первой строки блока: дата время категория сумма
    # Например: "26.05.2026 13:35 Рестораны и кафе 218,00"
    block_pattern = re.compile(
        r'^(\d{2}\.\d{2}\.\d{4})\s+'     # дата
        r'\d{2}:\d{2}\s+'                 # время
        r'(.+?)\s+'                        # категория сбера
        r'([+\-]?\s*[\d\s]+[,\.]\d{2})$' # сумма
    )

    operations = []
    i = 0
    while i < len(lines):
        m = block_pattern.match(lines[i])
        if m:
            date_str  = m.group(1)          # "26.05.2026"
            sber_cat  = m.group(2).strip()  # "Рестораны и кафе"
            amount_str = m.group(3).strip() # "+1 000,00" или "218,00"

            # Следующая строка — описание операции
            desc = lines[i + 1].strip() if i + 1 < len(lines) else ""

            i += 2  # переходим к следующему блоку

            # ── Определяем знак суммы ──────────────────────────────────────
            is_income = amount_str.startswith("+")
            clean_amount = re.sub(r'[+\-\s]', '', amount_str).replace(',', '.')
            try:
                amount = float(clean_amount)
            except ValueError:
                continue
            if amount <= 0:
                continue

            desc_lower = desc.lower()

            # ── 1. Внутренние переброски между своими счетами ──────────────
            if any(m in desc_lower for m in ["vklad-karta", "karta-vklad", "sberbank onl@in"]):
                operations.append({
                    "дата": date_str,
                    "сумма": amount,
                    "тип": "между счетами",
                    "категория": "Между счетами",
                    "подкатегория": "",
                    "магазин": "",
                    "описание": "Переброска между своими счетами",
                    "получатель": "",
                    "уверенность": 1.0,
                })
                continue

            # ── 2. Пропускаем "Без идентификации" ─────────────────────────
            if "без идентификации" in desc_lower:
                continue

            # ── 3. Продуктовые супермаркеты — пропускаем ──────────────────
            if any(s in desc_lower for s in SKIP_MERCHANTS):
                logger.info(f"Пропускаем супермаркет: {desc}")
                continue

            # ── 4. Наличные (банкомат) ─────────────────────────────────────
            if "atm" in desc_lower or "банкомат" in desc_lower or "выдача наличных" in desc_lower or sber_cat.lower() == "выдача наличных":
                operations.append({
                    "дата": date_str,
                    "сумма": amount,
                    "тип": "наличные",
                    "категория": "Наличные",
                    "подкатегория": "",
                    "магазин": desc,
                    "описание": "",
                    "получатель": "",
                    "уверенность": 1.0,
                })
                continue

            # ── 5. Определяем тип операции ────────────────────────────────
            op_type = "доход" if is_income else "расход"

            # ── 6. Переводы людям (ищем паттерн "Перевод от/для Имя") ─────
            recv = ""
            shop = ""
            category = ""

            transfer_match = re.search(
                r'перевод\s+(?:от|для|для\s+\w+\.)\s+(.+?)(?:\.|$)',
                desc_lower
            )
            yandex_match = "яндекс" in desc_lower

            if yandex_match:
                if is_income:
                    recv = "Алексей П."
                    category = "Доход"
                else:
                    shop = "Яндекс"
                    category = "Подписки"

            elif transfer_match or "перевод" in desc_lower:
                # Извлекаем имя получателя/отправителя из описания
                # Паттерны Сбера: "Перевод для П. Маргарита Алексеевна"
                #                  "Перевод от Ш. Диана Александровна"
                name_match = re.search(
                    r'(?:от|для)\s+(?:\w+\.\s+)?([А-ЯЁа-яё]+(?:\s+[А-ЯЁа-яё]+)*)',
                    desc, re.IGNORECASE
                )
                raw_name = name_match.group(1).strip() if name_match else ""
                family = normalize_family_name(raw_name) if raw_name else None

                if family:
                    recv = family
                    category = family_category(family, op_type)
                elif raw_name:
                    # Не семья — частник/ИП → в магазин
                    parts = raw_name.split()
                    if len(parts) >= 2:
                        shop = f"{parts[0]} {parts[1][0]}."
                    else:
                        shop = raw_name
                    category = "Переводы" if op_type == "расход" else "Доход"
                else:
                    category = "Переводы" if op_type == "расход" else "Доход"

            else:
                # ── 7. Магазины, кафе и прочее ────────────────────────────
                # Убираем "ГОРОД RUS" из названия
                clean_desc = re.sub(r'\s+(?:STERLITAMAK|MOSCOW|SPB|RUS)\s*$', '', desc, flags=re.IGNORECASE).strip()
                shop = clean_desc

                sber_cat_lower = sber_cat.lower()
                if "ресторан" in sber_cat_lower or "кафе" in sber_cat_lower:
                    category = "Кафе"
                elif "одежда" in sber_cat_lower or "аксессуар" in sber_cat_lower:
                    category = "Одежда"
                elif "транспорт" in sber_cat_lower:
                    category = "Транспорт"
                elif "медицин" in sber_cat_lower or "аптека" in sber_cat_lower:
                    category = "Медицина"
                elif "дом" in sber_cat_lower:
                    category = "Дом"
                elif "супермаркет" in sber_cat_lower:
                    category = "Продукты"
                else:
                    category = "Прочее"

            operations.append({
                "дата": date_str,
                "сумма": amount,
                "тип": op_type,
                "категория": category,
                "подкатегория": "",
                "магазин": shop,
                "описание": "",
                "получатель": recv,
                "уверенность": 1.0,
            })
        else:
            i += 1

    logger.info(f"Распознано операций: {len(operations)}")
    return operations


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
            await update.message.reply_text("📄 Анализирую операции Сбербанка...")
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
                "Возможно все операции — продуктовые магазины или переброски между счетами."
            )
            return

        total_expense  = sum(op["сумма"] for op in operations if op.get("тип") == "расход")
        total_income   = sum(op["сумма"] for op in operations if op.get("тип") == "доход")
        internal_count = sum(1 for op in operations if op.get("тип") == "между счетами")
        cash_count     = sum(1 for op in operations if op.get("тип") == "наличные")

        preview_lines = ["📋 *Найдены операции:*\n"]
        for op in operations[:10]:
            тип = op.get("тип", "")
            emoji = {"доход": "💰", "между счетами": "🔄", "наличные": "🏧"}.get(тип, "💸")
            показать = op.get("получатель") or op.get("магазин") or op.get("описание") or "—"
            preview_lines.append(
                f"{emoji} {показать[:40]} — {op['сумма']:,.0f} ₽ ({op.get('категория', '')})"
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
            msg += f"\n🔄 Переброски между счетами: {internal_count} шт"
        if cash_count:
            msg += f"\n🏧 Снятие наличных: {cash_count} раз"
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
