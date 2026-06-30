"""
Обработчик бюджетов по категориям.
Лимиты хранятся в листе БЮДЖЕТ Google Sheets.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import (
    get_sheets_client, SPREADSHEET_ID, MONTH_NAMES_RU, now_ufa
)

logger = logging.getLogger(__name__)

CATEGORIES = [
    "Продукты", "Кафе", "Бытовая химия", "Красота", "Одежда",
    "Алкоголь", "Табак", "Аптека", "Медицина", "Обучение",
    "Подписки ИИ", "Подписки", "Развлечения", "Животные",
    "Транспорт", "Ипотека", "Коммуналка", "Интернет", "Связь",
    "Страховка", "Переводы", "Электротовары", "Прочее",
]


def _get_budget_sheet():
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    try:
        sheet = spreadsheet.worksheet("БЮДЖЕТ")
    except Exception:
        sheet = spreadsheet.add_worksheet(title="БЮДЖЕТ", rows=50, cols=4)
        sheet.append_row(["Категория", "Лимит (₽)", "Примечание", "Обновлено"])
    return sheet


def get_budgets() -> dict:
    """Возвращает словарь {категория: лимит}."""
    try:
        sheet = _get_budget_sheet()
        rows = sheet.get_all_values()
        budgets = {}
        for row in rows[1:]:
            if len(row) >= 2 and row[0] and row[1]:
                try:
                    budgets[row[0].strip()] = float(
                        str(row[1]).replace(" ", "").replace(",", ".")
                    )
                except ValueError:
                    pass
        return budgets
    except Exception as e:
        logger.error(f"Ошибка get_budgets: {e}")
        return {}


def set_budget(category: str, limit: float) -> bool:
    """Устанавливает или обновляет лимит для категории."""
    try:
        sheet = _get_budget_sheet()
        rows = sheet.get_all_values()
        now = now_ufa().strftime("%d.%m.%Y")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0].strip().lower() == category.lower():
                sheet.update_cell(i, 2, limit)
                sheet.update_cell(i, 4, now)
                return True
        sheet.append_row([category, limit, "", now])
        return True
    except Exception as e:
        logger.error(f"Ошибка set_budget: {e}")
        return False


def delete_budget(category: str) -> bool:
    """Удаляет лимит для категории."""
    try:
        sheet = _get_budget_sheet()
        rows = sheet.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0].strip().lower() == category.lower():
                sheet.delete_rows(i)
                return True
        return False
    except Exception as e:
        logger.error(f"Ошибка delete_budget: {e}")
        return False


def check_budget_alert(category: str, spent: float, limit: float) -> str | None:
    """Возвращает текст предупреждения если потрачено ≥ 80% лимита."""
    if limit <= 0:
        return None
    pct = spent / limit * 100
    if pct >= 100:
        over = spent - limit
        return (
            f"🔴 *{category}*: лимит превышен!\n"
            f"Потрачено {spent:,.0f} ₽ из {limit:,.0f} ₽ (перерасход {over:,.0f} ₽)\n"
            "Герман смотрит осуждающе 😬"
        )
    elif pct >= 80:
        left = limit - spent
        return (
            f"⚠️ *{category}*: {pct:.0f}% лимита использовано\n"
            f"Осталось {left:,.0f} ₽ из {limit:,.0f} ₽"
        )
    return None


def _budget_target_month():
    """Если конец месяца (25+), показываем бюджет следующего месяца."""
    now = now_ufa()
    if now.day >= 25:
        if now.month == 12:
            return 1, now.year + 1
        return now.month + 1, now.year
    return now.month, now.year


async def handle_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущие бюджеты и их исполнение."""
    from services.sheets_service import get_monthly_report
    msg = update.message
    await msg.reply_text("💼 Герман проверяет бюджеты...")

    budgets = get_budgets()
    if not budgets:
        await msg.reply_text(
            "📭 Бюджеты не заданы.\n\n"
            "Напиши, например:\n"
            "_бюджет Продукты 15000_\n"
            "_бюджет Кафе 3000_",
            parse_mode="Markdown"
        )
        return

    month, year = _budget_target_month()
    now = now_ufa()
    report = get_monthly_report(month=month, year=year)
    cats = report.get("все_категории", {}) if "ошибка" not in report else {}

    # Строим таблицу в моноширинном шрифте
    header = f"💼 Бюджет на {MONTH_NAMES_RU[month]} {year}\n"

    rows = []
    total_spent = 0
    total_limit = 0
    for cat, limit in sorted(budgets.items()):
        spent = cats.get(cat, 0)
        left = limit - spent
        total_spent += spent
        total_limit += limit

        if spent >= limit:
            dot = "🔴"
            left_str = f"−{abs(left):,.0f}"
        elif spent >= limit * 0.8:
            dot = "🟡"
            left_str = f"+{left:,.0f}"
        else:
            dot = "🟢"
            left_str = f"+{left:,.0f}"

        rows.append((dot, cat, spent, limit, left_str))

    # Ширины колонок
    max_cat   = max(len(r[1]) for r in rows)
    col_cat   = max(max_cat, 10)

    table_lines = []
    table_lines.append(f"{'Категория':<{col_cat}}  {'Факт':>7}  {'Лимит':>7}  {'Осталось':>9}")
    table_lines.append("─" * (col_cat + 30))
    for dot, cat, spent, limit, left_str in rows:
        table_lines.append(
            f"{dot} {cat:<{col_cat-2}}  {spent:>6,.0f}  {limit:>6,.0f}  {left_str:>9}"
        )

    # Итого
    total_left = total_limit - total_spent
    left_sign = "+" if total_left >= 0 else "−"
    table_lines.append("─" * (col_cat + 30))
    table_lines.append(
        f"{'ИТОГО':<{col_cat}}  {total_spent:>6,.0f}  {total_limit:>6,.0f}  {left_sign}{abs(total_left):,.0f}"
    )

    table = "```\n" + "\n".join(table_lines) + "\n```"
    await msg.reply_text(header + table, parse_mode="Markdown")


async def handle_budget_set(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            category: str, limit: float):
    """Устанавливает лимит и отвечает."""
    ok = set_budget(category, limit)
    if ok:
        await update.message.reply_text(
            f"✅ Лимит установлен: *{category}* — {limit:,.0f} ₽/месяц\n"
            "Герман запомнил и будет следить 👀",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Не удалось сохранить лимит.")


async def handle_budget_delete(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               category: str):
    """Удаляет лимит категории."""
    ok = delete_budget(category)
    if ok:
        await update.message.reply_text(
            f"✅ Лимит для *{category}* удалён — Герман больше не следит за этой статьёй.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"Лимит для *{category}* не найден.", parse_mode="Markdown")
