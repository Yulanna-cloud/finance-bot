import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"


# =====================
# ПОСЛЕДНИЕ ГРУППЫ (ЧЕК = 1 GROUP_ID = 1 КНОПКА)
# =====================
def get_last_groups(limit=3):
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

    rows = sheet.get_all_values()

    if len(rows) <= 1:
        return []

    idx_group = 1
    idx_date = 2

    groups = {}

    # ВАЖНО: идём с конца — это фикс "последние записи"
    for row in reversed(rows[1:]):
        if len(row) <= idx_group:
            continue

        gid = row[idx_group]
        if not gid:
            continue

        # сохраняем только первую найденную строку группы (это "чек")
        if gid not in groups:
            groups[gid] = row

        if len(groups) >= limit:
            break

    return list(groups.items())


# =====================
# МЕНЮ УДАЛЕНИЯ
# =====================
async def show_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = get_last_groups(3)

    if not groups:
        await update.message.reply_text("Нет операций")
        return

    keyboard = []

    for group_id, row in groups:
        desc = row[13] if len(row) > 13 else "Операция"
        amount = row[7] if len(row) > 7 else "0"
        date = row[2] if len(row) > 2 else ""

        text = f"{desc} — {amount} ₽ | {date}"

        keyboard.append([
            InlineKeyboardButton(text, callback_data=f"delete_group:{group_id}")
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Отмена", callback_data="delete_cancel")
    ])

    await update.message.reply_text(
        "Выбери что удалить:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =====================
# УДАЛЕНИЕ (ЧЕК ЦЕЛИКОМ)
# =====================
async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "delete_cancel":
        await query.edit_message_text("Удаление отменено")
        return

    if not data.startswith("delete_group:"):
        return

    group_id = data.split(":")[1]

    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

    rows = sheet.get_all_values()

    new_rows = []
    deleted = 0

    for row in rows:
        if len(row) <= 1:
            new_rows.append(row)
            continue

        if row[1] == group_id:
            deleted += 1
            continue

        new_rows.append(row)

    sheet.clear()
    if new_rows:
        sheet.append_rows(new_rows)

    await query.edit_message_text(f"Удалено записей (чек целиком): {deleted}")
