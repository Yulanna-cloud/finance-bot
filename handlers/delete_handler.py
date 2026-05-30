"""
Обработчик команды /delete — удаление последних сессий ввода.
Одна сессия = вся выписка / весь чек / одна голосовая / одна текстовая запись.
Группируем по источнику + время записи (с точностью до минуты).
"""

import logging
from datetime import datetime
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"

SOURCE_LABELS = {
    "выписка_сбер":  "📄 Выписка Сбербанка",
    "голос":         "🎤 Голосовое сообщение",
    "telegram_текст":"💬 Текстовое сообщение",
    "telegram":      "💬 Текстовое сообщение",
    "фото":          "📷 Фото чека",
    "чек":           "📷 Фото чека",
}


def get_recent_sessions(n: int = 3) -> list:
    """
    Возвращает последние N сессий ввода.
    Сессия = уникальная пара (источник + время записи до минуты).
    """
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        all_values = sheet.get_all_values()

        if len(all_values) <= 1:
            return []

        headers = [h.strip().lower() for h in all_values[0]]
        data_rows = all_values[1:]

        def find_col(keywords, default):
            for i, h in enumerate(headers):
                if any(k in h for k in keywords):
                    return i
            return default

        i_source   = find_col(["источник"], 17)
        i_recorded = find_col(["дата"], 26)   # колонка AA — время записи в таблицу
        i_date     = find_col(["дата"],  2)   # колонка C — дата операции
        i_amount   = find_col(["сумма"], 7)
        i_cat      = find_col(["категори"], 9)

        # Группируем строки по ключу (источник + время_записи_до_минуты)
        # Порядок сохраняем — идём с конца
        sessions = {}   # key -> {rows, source, recorded_at, examples}
        session_order = []

        for row_idx, row in enumerate(data_rows):
            actual_row = row_idx + 2  # номер строки в Google Sheets

            def safe(i):
                return row[i].strip() if i < len(row) else ""

            source    = safe(i_source)
            recorded  = safe(i_recorded)
            op_date   = safe(i_date)
            amount    = safe(i_amount)
            cat       = safe(i_cat)

            # Ключ сессии: источник + время записи (до минуты)
            # Если времени нет — используем номер строки как уникальный ключ
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
                }
                session_order.append(key)

            sessions[key]["rows"].append(actual_row)
            sessions[key]["count"] += 1
            try:
                sessions[key]["total"] += float(amount.replace(" ", "").replace(",", "."))
            except (ValueError, TypeError):
                pass

        # Берём последние N сессий
        last_keys = session_order[-n:]
        result = []
        for key in reversed(last_keys):
            s = sessions[key]
            source_label = SOURCE_LABELS.get(s["source"], s["source"] or "Неизвестно")
            result.append({
                "key": key,
                "rows": s["rows"],
                "source": s["source"],
                "label": source_label,
                "recorded_at": s["recorded_at"],
                "op_date": s["op_date"],
                "count": s["count"],
                "total": s["total"],
            })

        return result

    except Exception as e:
        logger.error(f"Ошибка get_recent_sessions: {e}")
        return []


def delete_rows(row_numbers: list) -> bool:
    """Удаляет строки по номерам (с конца, чтобы не сбивать нумерацию)."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        for row in sorted(row_numbers, reverse=True):
            sheet.delete_rows(row)
        logger.info(f"Удалены строки: {row_numbers}")
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления строк {row_numbers}: {e}")
        return False


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последние 3 сессии ввода с кнопками удаления."""
    await update.message.reply_text("🔍 Загружаю последние записи...")

    sessions = get_recent_sessions(3)

    if not sessions:
        await update.message.reply_text("📭 В таблице нет операций для удаления.")
        return

    keyboard = []
    for i, s in enumerate(sessions):
        # Формируем подпись кнопки
        date_str = s["op_date"] or s["recorded_at"][:10]
        count_str = f"{s['count']} стр." if s["count"] > 1 else "1 запись"
        total_str = f"{s['total']:,.0f} ₽" if s["total"] > 0 else ""
        btn_text = f"{s['label']} | {date_str} | {count_str}"
        if total_str:
            btn_text += f" | {total_str}"

        keyboard.append([
            InlineKeyboardButton(
                text=btn_text[:60],
                callback_data=f"del_{i}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Отмена", callback_data="del_cancel")
    ])

    # Сохраняем сессии в context для callback
    context.user_data["delete_sessions"] = sessions

    await update.message.reply_text(
        "🗑 *Выбери что удалить:*\n_(последние 3 сессии ввода)_",
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
            idx = int(data.replace("del_", ""))
        except ValueError:
            await query.edit_message_text("❌ Ошибка.")
            return

        sessions = context.user_data.get("delete_sessions", [])
        if idx >= len(sessions):
            await query.edit_message_text("❌ Сессия не найдена. Попробуй /delete снова.")
            return

        session = sessions[idx]
        rows = session["rows"]

        await query.edit_message_text(f"⏳ Удаляю {len(rows)} строк...")

        success = delete_rows(rows)

        if success:
            await query.edit_message_text(
                f"✅ Удалено!\n\n"
                f"*{session['label']}*\n"
                f"Строк удалено: {len(rows)}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не удалось удалить. Попробуй ещё раз.")
