"""
Обработчик команды /delete — удаление последних записей из таблицы.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from services.sheets_service import get_sheets_client

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"


def get_last_operations(n: int = 7) -> list:
    """Возвращает последние N операций из таблицы с номерами строк."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        all_values = sheet.get_all_values()

        if len(all_values) <= 1:
            return []

        headers = all_values[0]
        data_rows = all_values[1:]

        # Определяем индексы нужных колонок
        def idx(names):
            for name in names:
                for i, h in enumerate(headers):
                    if name.lower() in h.lower():
                        return i
            return None

        i_date   = idx(["дата"])       or 2
        i_type   = idx(["тип"])        or 6
        i_amount = idx(["сумма"])      or 7
        i_cat    = idx(["категори"])   or 9
        i_shop   = idx(["магазин"])    or 12
        i_recv   = idx(["получател"])  or 14
        i_send   = idx(["отправител"]) or 27

        result = []
        # Берём последние N строк (индекс строки в таблице = индекс в data_rows + 2)
        for row_idx, row in enumerate(data_rows[-n:], start=len(data_rows) - min(n, len(data_rows)) + 2):
            def safe(i):
                return row[i].strip() if i is not None and i < len(row) else ""

            date    = safe(i_date)
            op_type = safe(i_type)
            amount  = safe(i_amount)
            cat     = safe(i_cat)
            shop    = safe(i_shop)
            recv    = safe(i_recv)
            sender  = safe(i_send)

            label = recv or sender or shop or cat or "—"
            emoji = "💰" if op_type == "доход" else "🔄" if op_type == "между счетами" else "🏧" if op_type == "наличные" else "💸"

            result.append({
                "row": row_idx,
                "text": f"{emoji} {date} | {amount} ₽ | {label[:25]}"
            })

        return result

    except Exception as e:
        logger.error(f"Ошибка get_last_operations: {e}")
        return []


def delete_row(row_number: int) -> bool:
    """Удаляет строку по номеру из листа ОПЕРАЦИИ."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        sheet.delete_rows(row_number)
        logger.info(f"Удалена строка {row_number}")
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления строки {row_number}: {e}")
        return False


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последние 7 записей с кнопками удаления."""
    await update.message.reply_text("🔍 Загружаю последние записи...")

    ops = get_last_operations(7)

    if not ops:
        await update.message.reply_text("📭 В таблице нет операций для удаления.")
        return

    keyboard = []
    for op in reversed(ops):  # свежие сверху
        keyboard.append([
            InlineKeyboardButton(
                text=op["text"],
                callback_data=f"del_{op['row']}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton("❌ Отмена", callback_data="del_cancel")
    ])

    await update.message.reply_text(
        "🗑 *Выбери запись для удаления:*\n_(показаны последние 7)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатие кнопки удаления."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "del_cancel":
        await query.edit_message_text("✅ Удаление отменено.")
        return

    if data.startswith("del_"):
        try:
            row_number = int(data.replace("del_", ""))
        except ValueError:
            await query.edit_message_text("❌ Ошибка.")
            return

        # Сохраняем текст кнопки чтобы показать что удалили
        deleted_text = ""
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == data:
                    deleted_text = btn.text
                    break

        success = delete_row(row_number)

        if success:
            await query.edit_message_text(
                f"✅ Удалено:\n_{deleted_text}_",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не удалось удалить. Попробуй ещё раз.")
