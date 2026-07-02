"""
Обработчик редактирования записей (/edit).
Позволяет изменить категорию, сумму, описание или получателя.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client, _get_cell, SPREADSHEET_ID, MONTH_NAMES_RU, now_ufa

logger = logging.getLogger(__name__)

# Поля, которые можно редактировать: (название, индекс колонки 0-based, номер колонки для update_cell)
EDITABLE_FIELDS = {
    "cat":   ("Категория",  9,  10),
    "sum":   ("Сумма",      7,   8),
    "desc":  ("Описание",  13,  14),
    "recv":  ("Получатель",14,  15),
}

CATEGORIES = [
    "Продукты", "Кафе", "Бытовая химия", "Бытовая техника", "Красота", "Одежда", "Дети",
    "Алкоголь", "Табак", "Аптека", "Медицина", "Обучение", "Подписки ИИ",
    "Подписки", "Развлечения", "Животные", "Транспорт", "Ипотека",
    "Коммуналка", "Интернет", "Связь", "Страховка", "Переводы",
    "Электротовары", "Прочее", "Доход",
]


def _get_recent_rows(n=10):
    """Возвращает последние n строк из ОПЕРАЦИИ с нужными полями."""
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")
    all_rows = sheet.get_all_values()
    if len(all_rows) <= 1:
        return [], sheet
    data_rows = all_rows[1:]
    recent = data_rows[-n:]
    result = []
    for i, row in enumerate(recent):
        row_idx = len(all_rows) - len(recent) + i + 1  # 1-based sheet row
        op_id   = _get_cell(row, 0)
        date    = _get_cell(row, 2)
        rtype   = _get_cell(row, 6)
        amount  = _get_cell(row, 7)
        cat     = _get_cell(row, 9)
        desc    = _get_cell(row, 13)
        recv    = _get_cell(row, 14)
        result.append({
            "row_idx": row_idx,
            "op_id": op_id,
            "date": date,
            "type": rtype,
            "amount": amount,
            "cat": cat,
            "desc": desc,
            "recv": recv,
        })
    return list(reversed(result)), sheet  # свежие сверху


def _build_edit_buttons(rows, offset=0, page_size=10):
    buttons = []
    for r in rows:
        emoji = "💰" if r["type"] == "доход" else "💸"
        label = f"{r['date']} | {emoji} {r['amount']} ₽ | {r['cat']}"
        if r["desc"]:
            label += f" — {r['desc'][:18]}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"edit_pick_{r['op_id']}")])
    return buttons


async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последние записи для выбора."""
    msg = update.message
    await msg.reply_text("✏️ Герман открывает последние записи...")
    try:
        rows, _ = _get_recent_rows(30)
    except Exception as e:
        await msg.reply_text(f"❌ Не удалось прочитать таблицу: {e}")
        return

    if not rows:
        await msg.reply_text("📭 Записей пока нет.")
        return

    context.user_data["edit_all_rows"] = rows
    await _show_edit_page(update.message, context, rows, page=0)


async def _show_edit_page(msg_or_query, context, rows, page=0):
    PAGE = 10
    start = page * PAGE
    chunk = rows[start:start + PAGE]
    buttons = _build_edit_buttons(chunk)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Назад", callback_data=f"edit_page_{page-1}"))
    if start + PAGE < len(rows):
        nav.append(InlineKeyboardButton("Ещё ▶", callback_data=f"edit_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel")])
    total = len(rows)
    text = f"Выбери запись ({start+1}–{min(start+PAGE, total)} из {total}):"
    markup = InlineKeyboardMarkup(buttons)
    if hasattr(msg_or_query, "edit_message_text"):
        await msg_or_query.edit_message_text(text, reply_markup=markup)
    else:
        await msg_or_query.reply_text(text, reply_markup=markup)


async def handle_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все этапы редактирования."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("edit_page_"):
        page = int(data.replace("edit_page_", ""))
        rows = context.user_data.get("edit_all_rows", [])
        if not rows:
            await query.edit_message_text("Список устарел — нажми ✏️ Изменить запись заново.")
            return
        await _show_edit_page(query, context, rows, page=page)
        return

    if data == "edit_cancel":
        context.user_data.pop("edit_state", None)
        await query.edit_message_text("Отменено — ничего не тронул 👌")
        return

    # Шаг 1: пользователь выбрал запись → показываем что редактировать
    if data.startswith("edit_pick_"):
        op_id = data.replace("edit_pick_", "")
        context.user_data["edit_state"] = {"op_id": op_id, "step": "choose_field"}
        buttons = [
            [InlineKeyboardButton("📂 Категория", callback_data=f"edit_field_cat_{op_id}")],
            [InlineKeyboardButton("💰 Сумма",     callback_data=f"edit_field_sum_{op_id}")],
            [InlineKeyboardButton("📝 Описание",  callback_data=f"edit_field_desc_{op_id}")],
            [InlineKeyboardButton("👤 Получатель",callback_data=f"edit_field_recv_{op_id}")],
            [InlineKeyboardButton("❌ Отмена",    callback_data="edit_cancel")],
        ]
        await query.edit_message_text(
            f"Запись *{op_id}* — что меняем?",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        return

    # Шаг 2: пользователь выбрал поле
    if data.startswith("edit_field_"):
        parts = data.replace("edit_field_", "").split("_", 1)
        field_key = parts[0]
        op_id = parts[1]
        field_name = EDITABLE_FIELDS.get(field_key, ("?",))[0]
        context.user_data["edit_state"] = {
            "op_id": op_id,
            "field_key": field_key,
            "step": "await_value"
        }

        # Для категории — показываем кнопки
        if field_key == "cat":
            rows = [CATEGORIES[i:i+3] for i in range(0, len(CATEGORIES), 3)]
            buttons = [
                [InlineKeyboardButton(c, callback_data=f"edit_setcat_{op_id}_{c}") for c in row]
                for row in rows
            ]
            buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel")])
            await query.edit_message_text(
                f"Выбери новую категорию для *{op_id}*:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                f"✏️ Напиши новое значение для поля *{field_name}* (запись *{op_id}*):",
                parse_mode="Markdown"
            )
        return

    # Шаг 2а: выбор категории из кнопок
    if data.startswith("edit_setcat_"):
        _, op_id, new_cat = data.split("_", 2)
        # убираем "edit_setcat" prefix
        op_id_clean = op_id
        new_cat_clean = new_cat
        await _do_update(query, context, op_id_clean, "cat", new_cat_clean)
        return


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Перехватывает текст когда ждём новое значение для редактирования.
    Возвращает True если сообщение обработано, False если нет.
    """
    state = context.user_data.get("edit_state")
    if not state or state.get("step") != "await_value":
        return False

    op_id     = state["op_id"]
    field_key = state["field_key"]
    new_value = update.message.text.strip()

    context.user_data.pop("edit_state", None)
    await _do_update_msg(update.message, context, op_id, field_key, new_value)
    return True


async def _do_update(query, context, op_id: str, field_key: str, new_value: str):
    """Обновляет ячейку через callback query."""
    context.user_data.pop("edit_state", None)
    field_name, col_idx, col_num = EDITABLE_FIELDS[field_key]
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")
        all_rows = sheet.get_all_values()
        row_num = None
        for i, row in enumerate(all_rows[1:], start=2):
            if _get_cell(row, 0) == op_id:
                row_num = i
                break
        if not row_num:
            await query.edit_message_text(f"❌ Запись {op_id} не найдена.")
            return
        sheet.update_cell(row_num, col_num, new_value)
        await query.edit_message_text(
            f"✅ Готово! В записи *{op_id}* поле *{field_name}* изменено на: *{new_value}*\n\n"
            "Герман обновил таблицу 📋",
            parse_mode="Markdown"
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")


async def _do_update_msg(msg, context, op_id: str, field_key: str, new_value: str):
    """Обновляет ячейку через текстовое сообщение."""
    field_name, col_idx, col_num = EDITABLE_FIELDS[field_key]
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")
        all_rows = sheet.get_all_values()
        row_num = None
        for i, row in enumerate(all_rows[1:], start=2):
            if _get_cell(row, 0) == op_id:
                row_num = i
                break
        if not row_num:
            await msg.reply_text(f"❌ Запись {op_id} не найдена.")
            return
        sheet.update_cell(row_num, col_num, new_value)
        await msg.reply_text(
            f"✅ Готово! В записи *{op_id}* поле *{field_name}* изменено на: *{new_value}*\n\n"
            "Герман обновил таблицу 📋",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.reply_text(f"❌ Ошибка: {e}")
