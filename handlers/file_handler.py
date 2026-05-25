"""
Обработчик файлов банковских выписок.
Поддерживает CSV и Excel от Сбербанка и Т-Банка.
"""

import logging
import io
import os
import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import parse_bank_statement
from services.sheets_service import write_operations_batch

logger = logging.getLogger(__name__)

# Максимум операций за раз (защита от огромных выписок)
MAX_OPERATIONS = 200


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or ""
    ext = os.path.splitext(filename)[1].lower()

    if ext not in (".csv", ".xlsx", ".xls"):
        await update.message.reply_text(
            "📄 Поддерживаю только файлы *CSV* и *Excel* (.xlsx, .xls)\n"
            "Именно в таком формате скачивается выписка из Сбербанка и Т-Банка.",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"📊 Читаю выписку *{filename}*...", parse_mode="Markdown")

    try:
        file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)

        # Читаем файл в DataFrame
        df = read_statement_file(buf, ext, filename)

        if df is None or df.empty:
            await update.message.reply_text(
                "❌ Не смогла прочитать файл. "
                "Убедись что это выписка из банка (Сбербанк или Т-Банк)."
            )
            return

        row_count = len(df)
        if row_count > MAX_OPERATIONS:
            await update.message.reply_text(
                f"⚠️ В файле {row_count} операций, обработаю первые {MAX_OPERATIONS}.\n"
                "Для больших выписок лучше загружать по месяцу."
            )
            df = df.head(MAX_OPERATIONS)

        await update.message.reply_text(
            f"📋 Найдено *{len(df)}* операций. Классифицирую через AI...\n"
            "_(Это займёт 10-30 секунд)_",
            parse_mode="Markdown"
        )

        # Конвертируем в текст для Gemini
        text_repr = df.to_string(index=False, max_rows=MAX_OPERATIONS)

        # Парсим через Gemini
        operations = parse_bank_statement(text_repr)

        if not operations:
            await update.message.reply_text(
                "❌ Не удалось классифицировать операции. "
                "Попробуй другой формат выписки."
            )
            return

        # Записываем
        ok, errors = write_operations_batch(operations, source="выписка_банка")

        # Краткая статистика
        total_expense = sum(
            op.get("сумма", 0) for op in operations
            if op.get("тип") == "расход"
        )
        total_income = sum(
            op.get("сумма", 0) for op in operations
            if op.get("тип") == "доход"
        )

        msg = (
            f"✅ Выписка обработана!\n\n"
            f"📥 Записано: *{ok}* операций\n"
            f"💸 Расходы: *{total_expense:,.0f} ₽*\n"
            f"💰 Доходы: *{total_income:,.0f} ₽*"
        )
        if errors:
            msg += f"\n⚠️ Не записалось: {errors}"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка handle_file: {e}")
        await update.message.reply_text(
            "❌ Ошибка при обработке файла. "
            "Попробуй сохранить выписку в формате CSV и загрузить снова."
        )


def read_statement_file(buf: io.BytesIO, ext: str, filename: str) -> pd.DataFrame:
    """
    Читает выписку в DataFrame.
    Пробует несколько вариантов кодировок и разделителей.
    """
    try:
        if ext in (".xlsx", ".xls"):
            engine = "openpyxl" if ext == ".xlsx" else "xlrd"
            # Пробуем с разными строками заголовка
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
            # CSV — пробуем разные кодировки
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
