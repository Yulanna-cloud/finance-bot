"""
Обработчик команды /delete.
Каждая позиция меню = один документ/ввод.
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client, _get_cell

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"

SOURCE_LABELS = {
    "выписка_сбер":   "📄 Выписка",
    "голос":          "🎤 Голос",
    "telegram_текст": "💬 Текст",
    "telegram":       "💬 Текст",
    "чек_фото":       "📷 Фото чека",
    "чек_qr":         "📷 QR-чек",
}

def get_source_label(source: str) -> str:
    """Возвращает метку для source, включая выписки с timestamp."""
    if source.startswith("выписка_сбер"):
        return "📄 Выписка"
    return SOURCE_LABELS.get(source, source or "Запись")

def normalize_date(val: str) -> str:
    """Приводит дату любого формата к DD.MM.YYYY"""
    if not val:
        return ""
    if len(val) >= 10 and val[2] == "." and val[5] == ".":
        return val[:10]
    try:
        return datetime.strptime(val[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return val[:10]

def normalize_time(val: str) -> str:
    """Приводит время к HH:MM"""
    if not val:
        return ""
    return val[:5]

def get_session_key(source: str, op_date: str, op_time: str, op_type: str) -> str:
    """
    Ключ сессии:
    - Выписка: группируем всё по source (содержит timestamp загрузки)
    - Остальные: источник + дата + время + тип
    """
    if source.startswith("выписка_сбер"):
        return source  # весь батч = одна сессия
    return f"{source}|{op_date}|{op_time}|{op_type}"


def get_recent_sessions(n: int = 3) -> list:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        all_values = sheet.get_all_values()

        if len(all_values) <= 1:
            return []

        headers = [h.strip().lower() for h in all_values[0]]

        def find_col(keywords, default):
            for i, h in enumerate(headers):
                if any(k in h for k in keywords):
                    return i
            return default

        i_source = find_col(["источник"], 17)
        i_date   = find_col(["дата"], 2)
        i_time   = find_col(["время"], 3)
        i_amount = find_col(["сумма"], 7)
        i_type   = find_col(["тип"], 6)
        i_cat    = find_col(["категори"], 9)
        i_desc   = find_col(["товар", "описани"], 13)
        i_shop   = find_col(["магазин"], 12)

        sessions = {}
        session_order = []

        for row_idx, row in enumerate(all_values[1:]):
            actual_row = row_idx + 2

            source   = _get_cell(row, i_source)
            op_date  = normalize_date(_get_cell(row, i_date))
            op_time  = normalize_time(_get_cell(row, i_time))
            amount   = _get_cell(row, i_amount)
            op_type  = _get_cell(row, i_type).lower()
            cat      = _get_cell(row, i_cat)
            desc     = _get_cell(row, i_desc)
            shop     = _get_cell(row, i_shop)

            key = get_session_key(source, op_date, op_time, op_type)

            if key not in sessions:
                sessions[key] = {
                    "rows": [],
                    "source": source,
                    "op_date": op_date,
                    "expense_total": 0.0,
                    "count": 0,
                    "first_cat": cat,
                    "first_desc": desc,
                    "first_shop": shop,
                }
                session_order.append(key)

            sessions[key]["rows"].append(actual_row)
            sessions[key]["count"] += 1

            if op_type not in ("между счетами", "наличные"):
                try:
                    amt = float(
                        amount.replace(" ", "").replace("\xa0", "").replace(",", ".")
                    )
                    sessions[key]["expense_total"] += amt
                except (ValueError, TypeError):
                    pass

        last_keys = session_order[-n:]
        result = []
        for key in reversed(last_keys):
            s = sessions[key]
            result.append(s | {"key": key})

        return result

    except Exception as e:
        logger.error(f"Ошибка get_recent_sessions: {e}")
        return []


def format_session_button(s: dict) -> str:
    source_label = get_source_label(s["source"])
    date_str = s["op_date"] or "?"
    total = s["expense_total"]
    count = s["count"]

    what = s["first_desc"] or s["first_shop"] or s["first_cat"] or ""
    if len(what) > 20:
        what = what[:20] + "…"

    total_str = f"{total:,.0f} ₽" if total > 0 else "0 ₽"

    if count == 1:
        label = f"{source_label}: {what} — {total_str} | {date_str}" if what else \
                f"{source_label}: {total_str} | {date_str}"
    else:
        label = f"{source_label}: {count} записей, {total_str} | {date_str}" if not what else \
                f"{source_label}: {what}… {count} зап., {total_str} | {date_str}"

    return label[:64]


def delete_rows(row_numbers: list) -> bool:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        for row in sorted(row_numbers, reverse=True):
            sheet.delete_rows(row)
        logger.info(f"Удалены строки: {row_numbers}")
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        return False


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Загружаю последние записи...")
    sessions = get_recent_sessions(3)

    if not sessions:
        await update.message.reply_text("📭 Нет операций для удаления.")
        return

    keyboard = []
    for i, s in enumerate(sessions):
        btn_text = format_session_button(s)
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_{i}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="del_cancel")])

    context.user_data["delete_sessions"] = sessions

    await update.message.reply_text(
        "🗑 *Выбери что удалить:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "del_cancel":
        await query.edit_message_text("✅ Отменено.")
        return

    if data.startswith("del_"):
        try:
            idx = int(data.replace("del_", ""))
        except ValueError:
            await query.edit_message_text("❌ Ошибка.")
            return

        sessions = context.user_data.get("delete_sessions", [])
        if idx >= len(sessions):
            await query.edit_message_text("❌ Попробуй /delete снова.")
            return

        session = sessions[idx]
        count = len(session["rows"])
        success = delete_rows(session["rows"])

        if success:
            source_label = get_source_label(session["source"])
            total_str = f" — {session['expense_total']:,.0f} ₽" if session["expense_total"] > 0 else ""
            await query.edit_message_text(
                f"✅ Удалено {count} записей\n"
                f"*{source_label}*{total_str}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не удалось удалить.")
