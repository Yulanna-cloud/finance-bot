import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"

# =========================
# ЛОГ УДАЛЕНИЙ (UNDO)
# =========================
LAST_DELETE_CACHE = {}  # group_id -> rows backup


# =========================
# ПОЛУЧИТЬ ПОСЛЕДНИЕ 3 ГРУППЫ
# =========================
def get_last_groups(limit=3):
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return []

    groups = {}

    for row in rows[1:]:
        if len(row) < 3:
            continue

        group_id = row[1]
        op_type = row[6].lower() if len(row) > 6 else ""

        # ❌ выкидываем мусорные группы
        if op_type in ("между счетами", "наличные"):
            continue

        if group_id not in groups:
            groups[group_id] = []

        groups[group_id].append(row)

    # ❗ фильтр: оставляем только реальные "документы"
    filtered_groups = {}

    for gid, items in groups.items():
        if len(items) == 0:
            continue

        # если 1 строка и это техническое — выкидываем
        if len(items) == 1:
            cat = items[0][9].lower() if len(items[0]) > 9 else ""
            if cat in ("между счетами", ""):
                continue

        filtered_groups[gid] = items

    def sort_key(item):
        rows = item[1]
        last = rows[-1]
        return last[2] if len(last) > 2 else ""

    sorted_groups = sorted(filtered_groups.items(), key=sort_key)

    return sorted_groups[-limit:]

# =========================
# МЕНЮ УДАЛЕНИЯ
# =========================
async def show_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = get_last_groups(3)

    if not groups:
        await update.message.reply_text("Нет операций")
        return

    keyboard = []

    for group_id, rows in reversed(groups):
        last = rows[-1]

        desc = last[12] if len(last) > 12 else "Операция"   # магазин
        amount = last[7] if len(last) > 7 else "0"
        date = last[2] if len(last) > 2 else ""

        # показываем ТОЛЬКО группу, не строки
        text = f"🧾 {desc} — {amount} ₽ | {date} (в группе {len(rows)} операций)"

        keyboard.append([
            InlineKeyboardButton(
                text,
                callback_data=f"delete_confirm:{group_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Отмена", callback_data="delete_cancel")
    ])

    await update.message.reply_text(
        "Выбери группу для удаления:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# CALLBACK УДАЛЕНИЯ
# =========================
async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    # отмена
    if data == "delete_cancel":
        await query.edit_message_text("Удаление отменено")
        return

    # подтверждение
    if data.startswith("delete_confirm:"):
        group_id = data.split(":")[1]

        client = get_sheets_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

        rows = sheet.get_all_values()

        to_delete = []
        new_rows = []

        for row in rows:
            if len(row) < 2:
                new_rows.append(row)
                continue

            if row[1] == group_id:
                to_delete.append(row)
                continue

            new_rows.append(row)

        # сохраняем для undo
        LAST_DELETE_CACHE[group_id] = to_delete

        sheet.clear()
        sheet.append_rows(new_rows)

        keyboard = [
            [InlineKeyboardButton("↩️ Отменить удаление", callback_data=f"undo_delete:{group_id}")],
            [InlineKeyboardButton("OK", callback_data="delete_done")]
        ]

        await query.edit_message_text(
            f"Удалено {len(to_delete)} операций",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # UNDO
    if data.startswith("undo_delete:"):
        group_id = data.split(":")[1]

        if group_id not in LAST_DELETE_CACHE:
            await query.edit_message_text("Нечего восстанавливать")
            return

        client = get_sheets_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

        restore_rows = LAST_DELETE_CACHE[group_id]

        sheet.append_rows(restore_rows)

        del LAST_DELETE_CACHE[group_id]

        await query.edit_message_text("Восстановлено")
        return

    if data == "delete_done":
        await query.edit_message_text("Готово")
        return
