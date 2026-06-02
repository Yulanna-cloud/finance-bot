import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"

# =========================
# UNDO CACHE (в памяти)
# =========================
LAST_DELETE_CACHE = {}  # group_id -> list[rows]


# =========================
# БЕЗОПАСНОЕ ЧТЕНИЕ ЯЧЕЕК
# =========================
def safe(row, idx, default=""):
    return row[idx] if len(row) > idx else default


# =========================
# ПОЛУЧЕНИЕ ГРУПП (ПРАВИЛЬНОЕ)
# =========================
def get_last_groups(limit=3):
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

    rows = sheet.get_all_values()

    if len(rows) <= 1:
        return []

    groups = {}

    # группируем ТОЛЬКО по group_id (колонка 1)
    for row in rows[1:]:
        if len(row) < 2:
            continue

        group_id = row[1]

        # пропуск пустых
        if not group_id:
            continue

        groups.setdefault(group_id, []).append(row)

    # сортировка групп по дате последней записи
    def sort_key(item):
        group_rows = item[1]
        last = group_rows[-1]
        return safe(last, 2)  # дата

    sorted_groups = sorted(groups.items(), key=sort_key)

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

        desc = safe(last, 12, "Операция")
        amount = safe(last, 7, "0")
        date = safe(last, 2, "")

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

    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")

    rows = sheet.get_all_values()

    # =====================
    # ОТМЕНА
    # =====================
    if data == "delete_cancel":
        await query.edit_message_text("Удаление отменено")
        return

    # =====================
    # УДАЛЕНИЕ ГРУППЫ
    # =====================
    if data.startswith("delete_confirm:"):
        group_id = data.split(":")[1]

        new_rows = []
        deleted_rows = []

        for row in rows:
            if len(row) < 2:
                new_rows.append(row)
                continue

            if row[1] == group_id:
                deleted_rows.append(row)
                continue

            new_rows.append(row)

        # backup для undo
        LAST_DELETE_CACHE[group_id] = deleted_rows

        sheet.clear()
        sheet.append_rows(new_rows)

        keyboard = [
            [InlineKeyboardButton("↩️ Отменить", callback_data=f"undo_delete:{group_id}")],
            [InlineKeyboardButton("OK", callback_data="delete_done")]
        ]

        await query.edit_message_text(
            f"Удалено операций: {len(deleted_rows)}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # =====================
    # UNDO
    # =====================
    if data.startswith("undo_delete:"):
        group_id = data.split(":")[1]

        if group_id not in LAST_DELETE_CACHE:
            await query.edit_message_text("Нечего восстанавливать")
            return

        restore_rows = LAST_DELETE_CACHE[group_id]

        sheet.append_rows(restore_rows)

        del LAST_DELETE_CACHE[group_id]

        await query.edit_message_text("Восстановлено")
        return

    # =====================
    # OK
    # =====================
    if data == "delete_done":
        await query.edit_message_text("Готово")
        return
