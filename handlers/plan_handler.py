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


async def _show_edit_keyboard(query_or_msg, context, edit_msg=False):
    """Показывает редактор плана — каждая категория кнопкой."""
    plan = context.user_data.get("pending_plan", {})
    cats = plan.get("категории", {})
    total = sum(cats.values())

    plan = context.user_data.get("pending_plan", {})
    income = plan.get("доход", 0)
    variable = plan.get("переменные", total)
    free = variable - total

    lines = ["✏️ *Редактор плана*\n",
             "Нажми на категорию чтобы изменить сумму:\n"]
    buttons = []
    for cat, amt in cats.items():
        lines.append(f"  • {cat}: *{amt:,.0f} ₽*")
        buttons.append([
            InlineKeyboardButton(f"✏️ {cat}: {amt:,.0f} ₽", callback_data=f"planedit_cat_{cat}"),
            InlineKeyboardButton("🗑", callback_data=f"planedit_del_{cat}"),
        ])

    lines.append(f"\n💰 Распределено: *{total:,.0f} ₽*")
    if free > 0:
        lines.append(f"🟢 Свободный резерв: *{free:,.0f} ₽*")
    elif free < 0:
        lines.append(f"🔴 Превышение бюджета: *{abs(free):,.0f} ₽* — Герман нервничает 😬")
    buttons.append([InlineKeyboardButton("➕ Добавить категорию", callback_data="planedit_add")])
    buttons.append([InlineKeyboardButton("✅ Сохранить и записать", callback_data="planedit_save")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="plan_cancel")])

    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(buttons)

    if hasattr(query_or_msg, "edit_message_text"):
        await query_or_msg.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await query_or_msg.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def handle_plan_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Перехватывает ввод при планировании (доход или новая сумма категории)."""
    step = context.user_data.get("plan_step")

    # Ввод дохода
    if step == "await_income":
        text = update.message.text.strip().replace(" ", "").replace(",", ".")
        try:
            income = float(text)
        except ValueError:
            await update.message.reply_text("Не понял сумму. Напиши просто число, например: 39000")
            return True
        month = context.user_data.pop("plan_month", now_ufa().month)
        context.user_data.pop("plan_step", None)
        await _show_plan(update.message, context, income, month)
        return True

    # Ввод новой суммы для категории в редакторе
    if step == "await_edit_amount":
        text = update.message.text.strip().replace(" ", "").replace(",", ".")
        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text("Только цифры, например: 8000")
            return True

        cat = context.user_data.pop("plan_edit_cat", None)
        context.user_data.pop("plan_step", None)
        if cat:
            plan = context.user_data.setdefault("pending_plan", {})
            plan.setdefault("категории", {})[cat] = amount

        # Показываем обновлённый редактор
        await update.message.reply_text(
            f"✅ *{cat}* → *{amount:,.0f} ₽*",
            parse_mode="Markdown"
        )
        await _show_edit_keyboard(update.message, context)
        return True

    return False


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
        await _show_edit_keyboard(query, context)
        return

    # Нажала на конкретную категорию в редакторе
    if data.startswith("planedit_cat_"):
        cat = data.replace("planedit_cat_", "")
        plan = context.user_data.get("pending_plan", {})
        cur = plan.get("категории", {}).get(cat, 0)
        context.user_data["plan_edit_cat"] = cat
        context.user_data["plan_step"] = "await_edit_amount"
        # Кнопки с быстрыми суммами + назад
        quick = [500, 1000, 1500, 2000, 3000, 5000, 7000, 10000]
        rows = [quick[i:i+4] for i in range(0, len(quick), 4)]
        buttons = [
            [InlineKeyboardButton(f"{v:,} ₽", callback_data=f"planedit_amt_{cat}_{v}") for v in row]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("◀ Назад без изменений", callback_data="planedit_back")])
        await query.edit_message_text(
            f"✏️ *{cat}*\nСейчас: *{cur:,.0f} ₽*\n\n"
            f"Выбери новую сумму кнопкой или напиши своё число и отправь сообщением:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # Быстрый выбор суммы кнопкой
    if data.startswith("planedit_amt_"):
        parts = data.replace("planedit_amt_", "").rsplit("_", 1)
        cat, amount_str = parts[0], parts[1]
        amount = float(amount_str)
        plan = context.user_data.setdefault("pending_plan", {})
        plan.setdefault("категории", {})[cat] = amount
        context.user_data.pop("plan_edit_cat", None)
        context.user_data.pop("plan_step", None)
        await _show_edit_keyboard(query, context)
        return

    # Добавить новую категорию
    if data == "planedit_add":
        context.user_data["plan_step"] = "await_new_cat"
        # Показываем список доступных категорий кнопками
        all_cats = CATEGORIES
        plan = context.user_data.get("pending_plan", {})
        existing = set(plan.get("категории", {}).keys())
        available = [c for c in all_cats if c not in existing]
        rows = [available[i:i+3] for i in range(0, len(available), 3)]
        buttons = [
            [InlineKeyboardButton(c, callback_data=f"planedit_newcat_{c}") for c in row]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("◀ Назад", callback_data="planedit_back")])
        await query.edit_message_text(
            "Выбери категорию для добавления в план:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("planedit_newcat_"):
        cat = data.replace("planedit_newcat_", "")
        context.user_data["plan_edit_cat"] = cat
        context.user_data["plan_step"] = "await_edit_amount"
        await query.edit_message_text(
            f"✏️ Добавляю *{cat}*\nВведи лимит (только цифры):",
            parse_mode="Markdown"
        )
        return

    if data == "planedit_back":
        await _show_edit_keyboard(query, context)
        return

    # Удалить категорию из плана
    if data.startswith("planedit_del_"):
        cat = data.replace("planedit_del_", "")
        plan = context.user_data.get("pending_plan", {})
        plan.get("категории", {}).pop(cat, None)
        await _show_edit_keyboard(query, context)
        return

    # Сохранить отредактированный план
    if data == "planedit_save":
        context.user_data.pop("plan_step", None)
        context.user_data.pop("plan_edit_cat", None)
        plan = context.user_data.pop("pending_plan", None)
        month = context.user_data.pop("pending_plan_month", now_ufa().month)
        if not plan:
            await query.edit_message_text("Что-то пошло не так — попробуй /plan заново.")
            return
        for cat, limit in plan.get("категории", {}).items():
            if limit > 0:
                set_budget(cat, limit)
        month_name = MONTH_NAMES_RU[month]
        await query.edit_message_text(
            f"✅ *Бюджет на {month_name} установлен!*\n\nГерман будет следить 👀",
            parse_mode="Markdown"
        )
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
