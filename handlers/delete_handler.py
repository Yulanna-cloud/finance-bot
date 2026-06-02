"""
Обработчик команды /delete.
"""

import logging
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

        i_source   = find_col(["источник"], 17)
        i_recorded = find_col(["дата создания", "дата запис"], 26)
        i_date     = find_col(["дата"], 2)
        i_amount   = find_col(["сумма"], 7)
        i_cat      = find_col(["категори"], 9)
        i_desc     = find_col(["товар", "описани"], 13)
        i_shop     = find_col(["магазин"], 12)

        sessions = {}
        session_order = []

        for row_idx, row in enumerate(all_values[1:]):
            actual_row = row_idx + 2

            source   = _get_cell(row, i_source)
            recorded = _get_cell(row, i_recorded)
            op_date  = _get_cell(row, i_date)
            amount   = _get_cell(row, i_amount)
            cat      = _get_cell(row, i_cat)
            desc     = _get_cell(row, i_desc)
            shop     = _get_cell(row, i_shop)

            rec_minute = recorded[:16] if len(recorded) >= 16 else recorded
            key = f"{source}|{rec_minute}"

            if key not in sessions:
                sessions[key] = {
                    "rows": [],
                    "source": source,
                    "recorded_at": recorded,
                    "op_date": op_date,
                    "total": 0.0,
                    "count": 0,
                    "first_cat": cat,
                    "first_desc": desc,
                    "first_shop": shop,
                }
                session_order.append(key)

            sessions[key]["rows"].append(actual_row)
            sessions[key]["count"] += 1
            try:
                sessions[key]["total"] += float(
                    amount.replace(" ", "").replace("\xa0", "").replace(",", ".")
                )
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
    source_label = SOURCE_LABELS.get(s["source"], s["source"] or "Запись")
    date_str = s["op_date"] or (s["recorded_at"][:10] if s["recorded_at"] else "?")
    total = s["total"]
    count = s["count"]

    what = s["first_desc"] or s["first_shop"] or s["first_cat"] or ""
    if len(what) > 25:
        what = what[:25] + "…"

    if count == 1:
        label = f"{source_label}: {what} — {total:,.0f} ₽ | {date_str}" if what else f"{source_label}: {total:,.0f} ₽ | {date_str}"
    else:
        label = f"{source_label}: {count} записей, {total:,.0f} ₽ | {date_str}"

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
        success = delete_rows(session["rows"])

        if success:
            total_str = f" — {session['total']:,.0f} ₽" if session["total"] > 0 else ""
            await query.edit_message_text(
                f"✅ Удалено {len(session['rows'])} записей\n"
                f"*{SOURCE_LABELS.get(session['source'], session['source'])}*{total_str}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не удалось удалить.")
