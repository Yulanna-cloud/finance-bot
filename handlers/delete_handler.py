import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"


# =====================
# ПОСЛЕДНИЕ 3 ГРУППЫ (ПОЛНЫЙ ФИКС: берем с конца таблицы)
# =====================
def get_last_groups(limit=3):
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

    rows = sheet.get_all_values()

    if len(rows) <= 1:
        return []

    idx_group = 1  # G-ID

    groups = {}

    # идем С КОНЦА таблицы (ключевой фикс)
    for row in reversed(rows[1:]):
        if len(row) <= idx_group:
            continue

        gid = row[idx_group]
        if not gid:
            continue

        if gid not in groups:
            groups[gid] = []

        groups[gid].append(row)

        if len(groups) >= limit:
            # не гарантирует ровно limit групп, но резко фиксит "старые сверху"
            pass

    # превращаем обратно в список групп
    result = list(groups.items())[:limit]

    return result


# =====================
# МЕНЮ УДАЛЕНИЯ
# =====================
async def show_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = get_last_groups(3)

    if not groups:
        await update.message.reply_text("Нет операций")
        return

    keyboard = []

    for group_id, rows in groups:
        last = rows[0]  # важно: первая из reversed = последняя в таблице

        desc = last[13] if len(last) > 13 else "Операция"
        amount = last[7] if len(last) > 7 else "0"
        date = last[2] if len(last) > 2 else ""

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
# УДАЛЕНИЕ
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

    await query.edit_message_text(f"Удалено записей: {deleted}")
