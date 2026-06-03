"""
Обработчик команды /delete.
- Показывает последние 3 сессии
- Просит подтверждение перед удалением
- Копирует строки в лист КОРЗИНА перед удалением
- /restore — восстанавливает последнее удаление
"""

import logging
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_sheets_client, _get_cell

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"
UFA_TZ = timezone(timedelta(hours=5))

TRASH_HEADERS = [
    "Дата удаления", "Источник", "Кол-во строк", "Сумма",
    "ID операций", "Данные строк (JSON)"
]

SOURCE_LABELS = {
    "выписка_сбер":   "📄 Выписка",
    "голос":          "🎤 Голос",
    "telegram_текст": "💬 Текст",
    "telegram":       "💬 Текст",
    "чек_фото":       "📷 Фото чека",
    "чек_qr":         "📷 QR-чек",
}

def get_source_label(source: str) -> str:
    if source.startswith("выписка_сбер"):
        return "📄 Выписка"
    return SOURCE_LABELS.get(source, source or "Запись")

def normalize_date(val: str) -> str:
    if not val:
        return ""
    if len(val) >= 10 and val[2] == "." and val[5] == ".":
        return val[:10]
    try:
        return datetime.strptime(val[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return val[:10]

def normalize_time(val: str) -> str:
    if not val:
        return ""
    return val[:5]

def get_session_key(source: str, op_date: str, op_time: str, op_type: str) -> str:
    if source.startswith("выписка_сбер"):
        return source
    return f"{source}|{op_date}|{op_time}|{op_type}"

def now_ufa() -> datetime:
    return datetime.now(tz=UFA_TZ)


def ensure_trash_sheet(spreadsheet) -> object:
    """Создаёт лист КОРЗИНА если его нет. Возвращает worksheet."""
    try:
        return spreadsheet.worksheet("КОРЗИНА")
    except Exception:
        sheet = spreadsheet.add_worksheet(title="КОРЗИНА", rows=1000, cols=10)
        sheet.append_row(TRASH_HEADERS)
        logger.info("Создан лист КОРЗИНА")
        return sheet


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
        # Сохраняем все строки чтобы потом скопировать в корзину
        all_rows_data = all_values  

        for row_idx, row in enumerate(all_values[1:]):
            actual_row = row_idx + 2

            source  = _get_cell(row, i_source)
            op_date = normalize_date(_get_cell(row, i_date))
            op_time = normalize_time(_get_cell(row, i_time))
            amount  = _get_cell(row, i_amount)
            op_type = _get_cell(row, i_type).lower()
            cat     = _get_cell(row, i_cat)
            desc    = _get_cell(row, i_desc)
            shop    = _get_cell(row, i_shop)

            key = get_session_key(source, op_date, op_time, op_type)

            if key not in sessions:
                sessions[key] = {
                    "rows": [],
                    "rows_data": [],  # данные строк для корзины
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
            sessions[key]["rows_data"].append(row)
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


def save_to_trash(spreadsheet, session: dict) -> bool:
    """Копирует строки сессии в КОРЗИНА перед удалением."""
    try:
        import json
        trash = ensure_trash_sheet(spreadsheet)
        now_str = now_ufa().strftime("%d.%m.%Y %H:%M")
        source_label = get_source_label(session["source"])

        # ID операций из первой колонки
        op_ids = [str(r[0]) if r else "" for r in session["rows_data"]]
        ids_str = ", ".join(op_ids[:10])
        if len(op_ids) > 10:
            ids_str += f"... (+{len(op_ids)-10})"

        # Данные строк как JSON
        rows_json = json.dumps(session["rows_data"], ensure_ascii=False)
        # Ограничиваем до 40000 символов (лимит ячейки Google Sheets)
        if len(rows_json) > 40000:
            rows_json = rows_json[:40000] + "...]"

        trash.append_row([
            now_str,
            source_label,
            session["count"],
            round(session["expense_total"], 2),
            ids_str,
            rows_json,
        ])
        logger.info(f"Сохранено в корзину: {session['count']} строк")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения в корзину: {e}")
        return False


def delete_rows(spreadsheet, row_numbers: list) -> bool:
    """Удаляет строки из листа ОПЕРАЦИИ."""
    try:
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        for row in sorted(row_numbers, reverse=True):
            sheet.delete_rows(row)
        logger.info(f"Удалены строки: {row_numbers}")
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        return False


def get_last_trash_entry(spreadsheet) -> dict | None:
    """Возвращает последнюю запись из КОРЗИНЫ для восстановления."""
    try:
        import json
        trash = ensure_trash_sheet(spreadsheet)
        all_rows = trash.get_all_values()
        if len(all_rows) <= 1:
            return None
        last = all_rows[-1]
        if len(last) < 6:
            return None
        rows_data = json.loads(last[5])
        return {
            "deleted_at": last[0],
            "source_label": last[1],
            "count": last[2],
            "total": last[3],
            "rows_data": rows_data,
            "trash_row": len(all_rows),  # номер строки в КОРЗИНЕ
        }
    except Exception as e:
        logger.error(f"Ошибка чтения корзины: {e}")
        return None


def restore_rows(spreadsheet, rows_data: list) -> bool:
    """Восстанавливает строки в лист ОПЕРАЦИИ."""
    try:
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        sheet.append_rows(rows_data, value_input_option="USER_ENTERED")
        logger.info(f"Восстановлено {len(rows_data)} строк")
        return True
    except Exception as e:
        logger.error(f"Ошибка восстановления: {e}")
        return False


def remove_last_trash_entry(spreadsheet, trash_row: int) -> None:
    """Удаляет запись из корзины после восстановления."""
    try:
        trash = spreadsheet.worksheet("КОРЗИНА")
        trash.delete_rows(trash_row)
    except Exception as e:
        logger.error(f"Ошибка очистки корзины: {e}")


# ─── Handlers ────────────────────────────────────────────────────────────────

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

    # Шаг 1: выбрали сессию → показываем подтверждение
    if data.startswith("del_") and not data.startswith("del_confirm_") and not data.startswith("del_cancel_"):
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
        source_label = get_source_label(session["source"])
        total_str = f"{session['expense_total']:,.0f} ₽" if session["expense_total"] > 0 else "0 ₽"
        count = session["count"]

        confirm_text = (
            f"⚠️ *Удалить {count} {'запись' if count == 1 else 'записей'}?*\n"
            f"{source_label} | {session['op_date'] or '?'} | {total_str}\n\n"
            f"Данные будут сохранены в корзину и их можно будет восстановить командой /restore"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑 Да, удалить", callback_data=f"del_confirm_{idx}"),
                InlineKeyboardButton("↩️ Назад", callback_data=f"del_back"),
            ]
        ])
        await query.edit_message_text(confirm_text, parse_mode="Markdown", reply_markup=keyboard)
        return

    # Шаг 2: нажали "Назад" → возвращаем список
    if data == "del_back":
        sessions = context.user_data.get("delete_sessions", [])
        if not sessions:
            await query.edit_message_text("❌ Попробуй /delete снова.")
            return
        keyboard = []
        for i, s in enumerate(sessions):
            btn_text = format_session_button(s)
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_{i}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="del_cancel")])
        await query.edit_message_text(
            "🗑 *Выбери что удалить:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Шаг 3: подтвердили → сохраняем в корзину и удаляем
    if data.startswith("del_confirm_"):
        try:
            idx = int(data.replace("del_confirm_", ""))
        except ValueError:
            await query.edit_message_text("❌ Ошибка.")
            return

        sessions = context.user_data.get("delete_sessions", [])
        if idx >= len(sessions):
            await query.edit_message_text("❌ Попробуй /delete снова.")
            return

        session = sessions[idx]
        await query.edit_message_text("⏳ Удаляю...")

        try:
            client = get_sheets_client()
            spreadsheet = client.open_by_key(SPREADSHEET_ID)

            # Сначала сохраняем в корзину
            save_to_trash(spreadsheet, session)

            # Потом удаляем
            success = delete_rows(spreadsheet, session["rows"])
        except Exception as e:
            logger.error(f"Ошибка при удалении: {e}")
            success = False

        if success:
            source_label = get_source_label(session["source"])
            total_str = f" — {session['expense_total']:,.0f} ₽" if session["expense_total"] > 0 else ""
            await query.edit_message_text(
                f"✅ Удалено {len(session['rows'])} записей\n"
                f"*{source_label}*{total_str}\n\n"
                f"_Восстановить: /restore_",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не удалось удалить.")


# ─── /restore ────────────────────────────────────────────────────────────────

async def handle_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Ищу последнее удаление...")

    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        entry = get_last_trash_entry(spreadsheet)
    except Exception as e:
        logger.error(f"Ошибка restore: {e}")
        await update.message.reply_text("❌ Ошибка при обращении к таблице.")
        return

    if not entry:
        await update.message.reply_text("📭 Корзина пуста — нечего восстанавливать.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Восстановить", callback_data="restore_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="restore_cancel"),
        ]
    ])

    context.user_data["restore_entry"] = entry

    await update.message.reply_text(
        f"♻️ *Последнее удаление:*\n"
        f"{entry['source_label']} | {entry['count']} записей | {entry['total']} ₽\n"
        f"Удалено: {entry['deleted_at']}\n\n"
        f"Восстановить в конец таблицы ОПЕРАЦИИ?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def handle_restore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "restore_cancel":
        await query.edit_message_text("✅ Отменено.")
        return

    if data == "restore_confirm":
        entry = context.user_data.get("restore_entry")
        if not entry:
            await query.edit_message_text("❌ Попробуй /restore снова.")
            return

        await query.edit_message_text("⏳ Восстанавливаю...")

        try:
            client = get_sheets_client()
            spreadsheet = client.open_by_key(SPREADSHEET_ID)
            success = restore_rows(spreadsheet, entry["rows_data"])
            if success:
                remove_last_trash_entry(spreadsheet, entry["trash_row"])
        except Exception as e:
            logger.error(f"Ошибка восстановления: {e}")
            success = False

        if success:
            await query.edit_message_text(
                f"✅ Восстановлено {entry['count']} записей!\n"
                f"_Строки добавлены в конец таблицы ОПЕРАЦИИ._",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не удалось восстановить.")
