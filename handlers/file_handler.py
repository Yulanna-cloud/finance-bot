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

# Продуктовые супермаркеты — пропускаем при загрузке выписки
# MAGAZIN убран — это реальные магазины, их надо записывать
SKIP_MERCHANTS = [
    "pyaterochka", "пятерочка", "magnit", "магнит", "krasnoe", "красное",
    "beloe", "белое", "находка", "ежик", "светофор", "монеточка",
    "perekrestok", "перекресток", "lenta", "лента", "вкусвилл", "spar",
    "дикси", "окей", "fix price", "самокат"
]

# Слова которые однозначно означают внутренний перевод между своими счетами
SKIP_INTERNAL = [
    "vklad-karta", "karta-vklad", "sberbank onl@in"
]


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

ПРАВИЛО 1 — ОБЯЗАТЕЛЬНО ПРОПУСТИ эти операции (не включай их в результат вообще):
- Любая строка где есть "SBERBANK ONL@IN" — это внутренний перевод между счетами одного человека, ПРОПУСТИ
- Любая строка где есть "VKLAD-KARTA" или "KARTA-VKLAD" — ПРОПУСТИ
- Любая строка где есть "PYATEROCHKA", "KRASNOE", "BELOE", "MAGNIT", "LENTA", "PEREKRESTOK", "DIXI", "SPAR" — это продуктовые магазины, ПРОПУСТИ
- Любая строка где есть "Нэсп Без Идентификации" или "Без Идентификации" — ПРОПУСТИ

ПРАВИЛО 2 — Определение типа операции:
- Сумма со знаком + в выписке = ДОХОД (тип "доход")
- Сумма без знака или с минусом = РАСХОД (тип "расход")
- ATM, банкомат, выдача наличных = тип "наличные"

ПРАВИЛО 3 — Поля магазин и описание:
- Если это покупка в магазине/кафе (не перевод человеку): поле "магазин" = название магазина (убери город и RUS), поле "описание" = ""
- Если это перевод человеку: поле "магазин" = "", поле "описание" = "", поле "получатель" = имя
- Примеры магазинов: "BUTIK LILIYA STERLITAMAK RUS" → магазин="Butik Liliya"
  "IP EFIMOVA STERLITAMAK RUS" → магазин="IP Efimova"
  "MIRA STERLITAMAK RUS" → магазин="Mira"
  "MAGAZIN 1 STERLITAMAK RUS" → магазин="Magazin 1"
  "KRASNOE&BELOE" → ПРОПУСТИ (продуктовый)

ПРАВИЛО 4 — Категории переводов людям:
- "Маргарита" → категория="Дети", получатель="Маргарита П.", тип="расход"
- "Диана Ш" или "Ш. Диана" → приход: категория="Доход", получатель="Диана Ш.", тип="доход"; расход: категория="Дети", получатель="Диана Ш.", тип="расход"
- "Алексей П" или "П. Алексей" → приход: категория="Доход", получатель="Алексей П.", тип="доход"; расход: категория="Переводы", получатель="Алексей П.", тип="расход"
- "Раиса" или "Г. Райса" → категория="Прочее", получатель="Раиса Г."
- "Яндекс" + ПРИХОД (знак +) → категория="Доход", получатель="Алексей П.", тип="доход", магазин="Яндекс"
- "Яндекс" + РАСХОД → категория="Подписки", магазин="Яндекс"
- Другие переводы людям → категория="Переводы", получатель=имя из описания

Формат каждого объекта:
{{"дата":"DD.MM.YYYY","сумма":число,"тип":"расход/доход/наличные","категория":"...","магазин":"...","описание":"","получатель":"..."}}

Категории: Кафе, Транспорт, Одежда, Дом, Медицина, Коммуналка, Переводы, Дети, Подписки, Доход, Наличные, Прочее

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

            описание = item.get("описание", "").lower()
            магазин = item.get("магазин", "").lower()
            получатель = item.get("получатель", "").lower()
            проверка = f"{описание} {магазин} {получатель}"

            # Фильтр внутренних переводов
            if any(s in проверка for s in SKIP_INTERNAL):
                logger.info(f"Пропускаем внутренний перевод: {item}")
                continue

            # Фильтр продуктовых магазинов
            if any(s in проверка for s in SKIP_MERCHANTS):
                logger.info(f"Пропускаем супермаркет: {item.get('магазин') or item.get('описание')}")
                continue

            # Пропускаем доходы без получателя и без магазина — это скорее всего внутренние переброски
            if item.get("тип") == "доход" and not item.get("получатель") and not item.get("магазин"):
                logger.info(f"Пропускаем доход без источника: {item}")
                continue

            result.append({
                "дата": item.get("дата", ""),
                "сумма": amount,
                "тип": item.get("тип", "расход"),
                "категория": item.get("категория", "Прочее"),
                "подкатегория": "",
                "магазин": item.get("магазин", ""),
                "описание": item.get("описание", ""),
                "получатель": item.get("получатель", ""),
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
        cash_count = sum(1 for op in operations if op.get("тип") == "наличные")

        preview_lines = ["📋 *Найдены операции:*\n"]
        for op in operations[:10]:
            тип = op.get("тип", "")
            emoji = "💰" if тип == "доход" else "🏧" if тип == "наличные" else "💸"
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
