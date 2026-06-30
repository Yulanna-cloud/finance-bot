"""
Обработчик планирования бюджета /plan.
В начале месяца спрашивает ожидаемый доход и расставляет лимиты.
"""
import logging
import calendar
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import now_ufa, MONTH_NAMES_RU, SPREADSHEET_ID, get_sheets_client
from handlers.budget_handler import set_budget, get_budgets

logger = logging.getLogger(__name__)

# Фиксированные расходы (не включаем в "лимиты" — они просто есть)
FIXED_EXPENSES = {
    "Ипотека":    6834,
    "Коммуналка": 2650,   # свет+газ+вода (среднее, летом меньше)
    "Интернет":    759,
    "Связь":       310,
    "Страховка":  1600,
}
FIXED_TOTAL = sum(FIXED_EXPENSES.values())  # ~12 153

# Цель по сбережениям
SAVINGS_GOAL = 5000

# Распределение переменного бюджета по категориям (в %)
# Сумма должна быть 100
VARIABLE_SPLIT = {
    "Продукты":      38,
    "Кафе":           9,
    "Бытовая химия":  7,
    "Красота":        7,
    "Одежда":         9,
    "Аптека":         5,
    "Животные":       5,
    "Обучение":       7,
    "Развлечения":    5,
    "Прочее":         8,
}

# Август — школьный месяц: меняем пропорции для Одежды и добавляем Дети
AUGUST_SPLIT = {
    **VARIABLE_SPLIT,
    "Одежда":         20,   # больше на школу
    "Продукты":       30,
    "Кафе":            5,
    "Развлечения":     3,
    "Прочее":          5,
}


def calc_plan(income: float, month: int) -> dict:
    """Рассчитывает план бюджета исходя из дохода и месяца."""
    variable = income - FIXED_TOTAL - SAVINGS_GOAL
    if variable < 0:
        variable = max(income - FIXED_TOTAL, 0)

    split = AUGUST_SPLIT if month == 8 else VARIABLE_SPLIT
    budgets = {}
    for cat, pct in split.items():
        budgets[cat] = round(variable * pct / 100 / 100) * 100  # округляем до 100₽

    return {
        "доход": income,
        "фиксированные": FIXED_TOTAL,
        "сбережения": SAVINGS_GOAL,
        "переменные": variable,
        "категории": budgets,
        "август": month == 8,
    }


def format_plan(plan: dict, month: int) -> str:
    month_name = MONTH_NAMES_RU[month]
    income = plan["доход"]
    fixed = plan["фиксированные"]
    savings = plan["сбережения"]
    variable = plan["переменные"]
    cats = plan["категории"]

    lines = [f"📋 *План бюджета на {month_name}*\n"]
    lines.append(f"💰 Доход: *{income:,.0f} ₽*")
    lines.append(f"🏠 Фиксированные: *{fixed:,.0f} ₽*")
    lines.append(f"  (ипотека, коммуналка, интернет, связь, страховка)")
    lines.append(f"💾 Откладываем: *{savings:,.0f} ₽*")
    lines.append(f"🟢 На жизнь остаётся: *{variable:,.0f} ₽*\n")

    if plan.get("август"):
        lines.append("🎒 *Август — школьный месяц!* Больше заложено на одежду для Риты.\n")

    lines.append("📂 *Предлагаю лимиты:*")
    for cat, amount in cats.items():
        lines.append(f"  • {cat}: *{amount:,.0f} ₽*")

    total_var = sum(cats.values())
    leftover = variable - total_var
    if leftover > 0:
        lines.append(f"\n  _+ {leftover:,.0f} ₽ резерв (непредвиденные расходы)_")

    lines.append("\nЗаписать эти лимиты в бюджет?")
    return "\n".join(lines)


def _target_month() -> tuple[int, int]:
    """Возвращает (месяц, год) для планирования: если 25+, то следующий месяц."""
    now = now_ufa()
    if now.day >= 25:
        if now.month == 12:
            return 1, now.year + 1
        return now.month + 1, now.year
    return now.month, now.year


async def handle_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает планирование бюджета."""
    msg = update.message
    month, year = _target_month()

    # Если в context.args есть доход — сразу считаем
    if context.args:
        try:
            income = float(context.args[0].replace(",", "."))
            await _show_plan(msg, context, income, month)
            return
        except ValueError:
            pass

    # Иначе спрашиваем доход
    month_name = MONTH_NAMES_RU[month]
    await msg.reply_text(
        f"📋 *Планируем бюджет на {month_name} {year}!*\n\n"
        f"Фиксированные расходы: ~*{FIXED_TOTAL:,.0f} ₽*\n"
        f"Хотим отложить: *{SAVINGS_GOAL:,.0f} ₽*\n\n"
        "Напиши ожидаемый доход:",
        parse_mode="Markdown"
    )
    context.user_data["plan_step"] = "await_income"
    context.user_data["plan_month"] = month


async def handle_plan_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Перехватывает ввод дохода при планировании."""
    if context.user_data.get("plan_step") != "await_income":
        return False

    text = update.message.text.strip().replace(" ", "").replace(",", ".")
    try:
        income = float(text)
    except ValueError:
        await update.message.reply_text("Не понял сумму. Напиши просто число, например: 39000")
        return True

    month = context.user_data.get("plan_month", now_ufa().month)
    context.user_data.pop("plan_step", None)
    context.user_data.pop("plan_month", None)
    await _show_plan(update.message, context, income, month)
    return True


async def _show_plan(msg, context, income: float, month: int):
    plan = calc_plan(income, month)
    context.user_data["pending_plan"] = plan
    context.user_data["pending_plan_month"] = month

    text = format_plan(plan, month)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Записать лимиты", callback_data="plan_confirm")],
        [InlineKeyboardButton("✏️ Изменить лимиты", callback_data="plan_edit")],
        [InlineKeyboardButton("❌ Отмена", callback_data="plan_cancel")],
    ])
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def handle_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "plan_cancel":
        context.user_data.pop("pending_plan", None)
        context.user_data.pop("pending_plan_month", None)
        await query.edit_message_text("Планирование отменено — лимиты не изменились.")
        return

    if data == "plan_edit":
        plan = context.user_data.get("pending_plan")
        if not plan:
            await query.edit_message_text("Что-то пошло не так — попробуй /plan заново.")
            return
        cats = plan["категории"]
        lines = ["✏️ *Изменить лимиты*\n",
                 "Напиши боту в формате:\n_бюджет Продукты 8000_\n",
                 "Текущий план:"]
        for cat, amt in cats.items():
            lines.append(f"  • {cat}: {amt:,.0f} ₽")
        lines.append("\nПосле правок нажми 💼 *Бюджет* чтобы проверить,")
        lines.append("или запусти /plan заново с другой суммой дохода.")
        # Сначала запишем план как есть, потом пользователь скорректирует
        for cat, limit in cats.items():
            if limit > 0:
                set_budget(cat, limit)
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
        return

    plan = context.user_data.pop("pending_plan", None)
    month = context.user_data.pop("pending_plan_month", now_ufa().month)

    if not plan:
        await query.edit_message_text("Что-то пошло не так — попробуй /plan заново.")
        return

    cats = plan["категории"]
    errors = []
    for cat, limit in cats.items():
        if limit > 0:
            ok = set_budget(cat, limit)
            if not ok:
                errors.append(cat)

    month_name = MONTH_NAMES_RU[month]
    if errors:
        await query.edit_message_text(
            f"⚠️ Большинство лимитов записано, но не удалось сохранить: {', '.join(errors)}"
        )
    else:
        await query.edit_message_text(
            f"✅ *Бюджет на {month_name} установлен!*\n\n"
            f"Герман будет следить за каждой категорией 👀\n"
            f"Посмотреть лимиты: кнопка 💼 *Бюджет*",
            parse_mode="Markdown"
        )


async def monthly_plan_reminder(context):
    """Напоминание в начале месяца — предложить запланировать бюджет."""
    chat_id = context.job.data
    now = now_ufa()
    month_name = MONTH_NAMES_RU[now.month]
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📋 Привет! Начался *{month_name}* — самое время запланировать бюджет.\n\n"
            "Нажми кнопку 📋 *Планирование* или напиши /plan\n"
            "Герман поможет распределить деньги так, чтобы ещё и отложить 💾"
        ),
        parse_mode="Markdown"
    )
