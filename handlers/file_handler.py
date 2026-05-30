"""
Обработчик файлов банковских выписок.
PDF Сбербанка парсится собственным кодом без ИИ.
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

SKIP_MERCHANTS = [
    "pyaterochka", "krasnoe", "beloe", "magnit", "lenta",
    "perekrestok", "dixi", "spar", "пятерочка", "магнит",
    "перекресток", "лента", "дикси", "находка", "ежик",
    "светофор", "монеточка", "вкусвилл", "окей", "fix price", "самокат",
]

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

def normalize_family_name(raw: str):
    r = raw.lower()
    for key, val in FAMILY_NAMES.items():
        if key in r:
            return val
    return None

def family_category(name: str, op_type: str) -> str:
    n = name.lower()
    if "маргарита" in n or "диана" in n:
        return "Дети" if op_type == "расход" else "Доход"
    if "алексей" in n:
        return "Переводы" if op_type == "расход" else "Доход"
    return "Семья"


def clean_shop_name(raw: str) -> str:
    """Очищает название магазина из выписки Сбербанка.
    '26.05.2026 190676 IP EFIMOVA STERLITAMAK RUS. Операция по карте ****0105'
    -> 'Ip Efimova'
    """
    s = raw.strip()
    # Убираем дату и код авторизации в начале
    s = re.sub(r'^\d{2}\.\d{2}\.\d{4}\s+\d{4,8}\s+', '', s)
    # Убираем всё начиная с ". Операция"
    s = re.sub(r'\.\s*Операция.*', '', s, flags=re.IGNORECASE)
    # Убираем город и RUS в конце
    s = re.sub(r'\s+(STERLITAMAK|MOSCOW|SPB|KAZAN|UFA|RUS).*', '', s, flags=re.IGNORECASE)
    # Убираем если осталось слово Операция
    s = re.sub(r'Операция.*', '', s, flags=re.IGNORECASE)
    return s.strip().title() or raw.strip()

def parse_sber_pdf(pdf_bytes: bytes) -> list:
    """
    Парсер выписки Сбербанка.
    
    Формат блока в PDF (после извлечения текста):
      Строка A: "28.05.2026 06:49 Прочие операции 500,00 88,59"
                 дата  время  категория_сбера  СУММА  остаток
      Строка B: "28.05.2026 398873 Яндекс. Операция по счету ****4953"
                 дата_обработки  код  ОПИСАНИЕ
    
    Сумма — это ПЕРВОЕ число в конце строки A (перед остатком).
    Остаток — ВТОРОЕ число в конце строки A (последнее).
    Знак + перед суммой = доход, без знака = расход.
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

    logger.info(f"PDF текст ({len(full_text)} символов):\n{full_text[:2000]}")

    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    # Паттерн строки A: дата время категория ... сумма остаток
    # Пример: "28.05.2026 06:49 Прочие операции 500,00 88,59"
    # Пример с +: "27.05.2026 18:02 Перевод СБП +500,00 4 188,59"
    line_a_re = re.compile(
        r'^(\d{2}\.\d{2}\.\d{4})\s+'   # дата операции
        r'\d{2}:\d{2}\s+'               # время
        r'(.+?)\s+'                     # категория сбера
        r'([+\-]?[\d\s]+[,\.]\d{2})'   # сумма операции (может быть +/-)
        r'\s+[\d\s]+[,\.]\d{2}$'       # остаток (последнее число — игнорируем)
    )

    # Паттерн строки B: дата_обработки код_авторизации описание
    # Пример: "28.05.2026 398873 Яндекс. Операция по счету ****4953"
    line_b_re = re.compile(
        r'^\d{2}\.\d{2}\.\d{4}\s+\d{4,8}\s+(.+)$'
    )

    operations = []
    i = 0
    while i < len(lines):
        m_a = line_a_re.match(lines[i])
        if not m_a:
            i += 1
            continue

        date_str  = m_a.group(1)
        sber_cat  = m_a.group(2).strip()
        amount_raw = m_a.group(3).strip()

        # Описание — следующая строка (строка B)
        desc = ""
        if i + 1 < len(lines):
            m_b = line_b_re.match(lines[i + 1])
            if m_b:
                desc = m_b.group(1).strip()
                i += 2
            else:
                i += 1
        else:
            i += 1

        # Знак и сумма
        is_income = amount_raw.startswith("+")
        clean_amount = re.sub(r'[+\-\s]', '', amount_raw).replace(',', '.')
        try:
            amount = float(clean_amount)
        except ValueError:
            continue
        if amount <= 0:
            continue

        desc_lower = desc.lower()
        op_type = "доход" if is_income else "расход"

        recv = ""      # получатель (семья)
        shop = ""      # магазин / отправитель
        sender = ""    # отправитель (для доходов)
        category = ""

        # ── 1. Переброски между своими счетами ────────────────────────────
        if any(m in desc_lower for m in ["vklad-karta", "karta-vklad", "sberbank onl@in"]):
            operations.append({
                "дата": date_str, "сумма": amount,
                "тип": "между счетами", "категория": "Между счетами",
                "подкатегория": "", "магазин": "", "описание": "Переброска между своими счетами",
                "получатель": "", "отправитель": "", "уверенность": 1.0,
            })
            continue

        # ── 2. Пропускаем "Без идентификации" ─────────────────────────────
        if "без идентификации" in desc_lower:
            continue

        # ── 3. Продуктовые супермаркеты — пропускаем ──────────────────────
        if any(s in desc_lower for s in SKIP_MERCHANTS):
            logger.info(f"Пропускаем супермаркет: {desc}")
            continue

        # ── 4. Наличные (банкомат) ─────────────────────────────────────────
        if "atm" in desc_lower or "выдача наличных" in sber_cat.lower():
            clean_desc = re.sub(
                r'\s*(STERLITAMAK|MOSCOW|SPB|RUS)\b.*', '', desc, flags=re.IGNORECASE
            ).strip()
            operations.append({
                "дата": date_str, "сумма": amount,
                "тип": "наличные", "категория": "Наличные",
                "подкатегория": "", "магазин": clean_desc, "описание": "",
                "получатель": "", "отправитель": "", "уверенность": 1.0,
            })
            continue

        # ── 5. Яндекс ──────────────────────────────────────────────────────
        if "яндекс" in desc_lower:
            if is_income:
                sender   = "Алексей П."
                category = "Доход"
                op_type  = "доход"
            else:
                shop     = "Яндекс"
                category = "Подписки"
                op_type  = "расход"
            operations.append({
                "дата": date_str, "сумма": amount,
                "тип": op_type, "категория": category,
                "подкатегория": "", "магазин": shop, "описание": "",
                "получатель": "", "отправитель": sender, "уверенность": 1.0,
            })
            continue

        # ── 6. Переводы людям ──────────────────────────────────────────────
        # Паттерны Сбера:
        #   "Перевод для П. Маргарита Алексеевна. Операция..."
        #   "Перевод от Ш. Диана Александровна. Операция..."
        #   "Перевод от П. Алексей Георгиевич. Операция..."
        transfer_re = re.compile(
            r'перевод\s+(?:для|от)\s+'
            r'(?:[А-ЯЁа-яё]\.\s+)?'           # необязательная первая буква фамилии
            r'([А-ЯЁа-яё][а-яё]+(?:\s+[А-ЯЁа-яё][а-яё]+)*)',  # имя [отчество]
            re.IGNORECASE
        )
        tr_match = transfer_re.search(desc)

        if tr_match or "перевод" in desc_lower or "перевод сбп" in sber_cat.lower() or "перевод с карты" in sber_cat.lower():
            raw_name = tr_match.group(1).strip() if tr_match else ""
            family = normalize_family_name(raw_name) if raw_name else None

            if family:
                if is_income:
                    # Деньги пришли от члена семьи
                    sender   = family
                    category = family_category(family, "доход")
                    op_type  = "доход"
                else:
                    # Перевели члену семьи
                    recv     = family
                    category = family_category(family, "расход")
                    op_type  = "расход"
            else:
                # Не семья — частник/ИП
                if raw_name:
                    parts = raw_name.split()
                    short = f"{parts[0]} {parts[1][0]}." if len(parts) >= 2 else raw_name
                    if is_income:
                        sender   = short
                        category = "Доход"
                        op_type  = "доход"
                    else:
                        shop     = short
                        category = "Переводы"
                        op_type  = "расход"
                else:
                    category = "Доход" if is_income else "Переводы"

            operations.append({
                "дата": date_str, "сумма": amount,
                "тип": op_type, "категория": category,
                "подкатегория": "", "магазин": shop, "описание": "",
                "получатель": recv, "отправитель": sender, "уверенность": 1.0,
            })
            continue

        # ── 7. Магазины и кафе ─────────────────────────────────────────────
        clean_desc = clean_shop_name(desc)

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
            "дата": date_str, "сумма": amount,
            "тип": op_type, "категория": category,
            "подкатегория": "", "магазин": clean_desc, "описание": "",
            "получатель": "", "отправитель": "", "уверенность": 1.0,
        })

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
                "❌ Не нашла операций.\n"
                "Возможно все — продуктовые магазины или переброски между счетами."
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
            показать = op.get("отправитель") or op.get("получатель") or op.get("магазин") or "—"
            preview_lines.append(
                f"{emoji} {показать[:35]} — {op['сумма']:,.0f} ₽ ({op.get('категория', '')})"
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
