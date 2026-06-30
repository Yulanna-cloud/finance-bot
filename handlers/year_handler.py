"""
Обработчики /year (итоги года) и /analiz (анализ трат).
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import get_year_summary, get_monthly_report, now_ufa, MONTH_NAMES_RU

logger = logging.getLogger(__name__)


async def handle_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Итоги года по месяцам."""
    now = now_ufa()
    year = now.year

    # Можно передать год: /year 2025
    if context.args:
        try:
            year = int(context.args[0])
        except ValueError:
            pass

    msg = update.message or update.callback_query.message
    await msg.reply_text(f"📅 Считаю итоги {year} года — секунду...")

    data = get_year_summary(year=year)
    if "ошибка" in data:
        await msg.reply_text(f"❌ Что-то пошло не так: {data['ошибка']}")
        return

    months = data["месяцы"]
    if not months:
        await msg.reply_text(f"📭 За {year} год данных пока нет.")
        return

    lines = [f"📊 *Итоги {year} года*\n"]
    for m in months:
        bal = m["остаток"]
        bal_emoji = "✅" if bal >= 0 else "🔴"
        sign = "+" if bal >= 0 else "−"
        lines.append(
            f"*{m['месяц']}*\n"
            f"  💰 Доход: {m['доходы']:,.0f} ₽\n"
            f"  💸 Расход: {m['расходы']:,.0f} ₽\n"
            f"  {bal_emoji} Остаток: {sign}{abs(bal):,.0f} ₽"
        )

    lines.append("")
    total_bal = data["итого_остаток"]
    bal_emoji = "✅" if total_bal >= 0 else "🔴"
    sign = "+" if total_bal >= 0 else "−"
    lines.append("─" * 22)
    lines.append(f"💰 *Всего доходов:* {data['итого_доходы']:,.0f} ₽")
    lines.append(f"💸 *Всего расходов:* {data['итого_расходы']:,.0f} ₽")
    lines.append(f"{bal_emoji} *Итог:* {sign}{abs(total_bal):,.0f} ₽")

    # Самый дорогой и самый экономный месяц
    if len(months) > 1:
        richest = max(months, key=lambda x: x["расходы"])
        cheapest = min(months, key=lambda x: x["расходы"])
        lines.append(f"\n🔝 Щедрее всего: *{richest['месяц']}* ({richest['расходы']:,.0f} ₽)")
        lines.append(f"🥇 Экономнее всего: *{cheapest['месяц']}* ({cheapest['расходы']:,.0f} ₽)")
        lines.append("_Герман запомнил, кто тут транжира 😄_")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_analiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализ трат текущего месяца с выводами от Германа."""
    now = now_ufa()
    msg = update.message or update.callback_query.message
    await msg.reply_text("🧠 Герман думает...")

    # Текущий и прошлый месяц
    cur = get_monthly_report(month=now.month, year=now.year)
    prev_m = now.month - 1 if now.month > 1 else 12
    prev_y = now.year if now.month > 1 else now.year - 1
    prev = get_monthly_report(month=prev_m, year=prev_y)

    if "ошибка" in cur or cur["количество"] == 0:
        await msg.reply_text("📭 Данных за текущий месяц пока нет — Герману не над чем думать 🤷")
        return

    cats = cur.get("все_категории", {})
    total_expense = cur["расходы"]
    total_income  = cur["доходы"]
    balance       = cur["остаток"]

    lines = [f"🧠 *Анализ — {MONTH_NAMES_RU[now.month]} {now.year}*\n"]

    # Приход / Расход / Остаток
    bal_emoji = "✅" if balance >= 0 else "🔴"
    bal_sign  = "+" if balance >= 0 else "−"
    lines.append("💰 *Приход и расход:*")
    lines.append(f"  💰 Доходы:  *{total_income:,.0f} ₽*")
    lines.append(f"  💸 Расходы: *{total_expense:,.0f} ₽*")
    lines.append(f"  {bal_emoji} Остаток:  *{bal_sign}{abs(balance):,.0f} ₽*")

    if balance < 0:
        lines.append("  _Расходы превысили доходы — Герман обеспокоен 😟_")
    elif balance < total_income * 0.1:
        lines.append("  _Осталось совсем чуть-чуть. Герман на нервах 😬_")
    else:
        lines.append("  _Остаток выглядит прилично. Герман спокоен 😌_")

    if not cats or total_expense == 0:
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Топ-3 категории по расходам (с нормализацией регистра)
    top3 = sorted(cats.items(), key=lambda x: x[1], reverse=True)[:3]
    lines.append("\n🔝 *Топ расходов:*")
    for i, (cat, amount) in enumerate(top3, 1):
        pct = amount / total_expense * 100
        cat_display = cat[0].upper() + cat[1:] if cat else cat
        lines.append(f"  {i}. {cat_display}: *{amount:,.0f} ₽* ({pct:.0f}%)")

    # Комментарий по топ-1
    top_cat, top_amount = top3[0]
    top_pct = top_amount / total_expense * 100
    top_display = top_cat[0].upper() + top_cat[1:] if top_cat else top_cat
    if top_pct > 40:
        lines.append(f"\n💬 *{top_display}* — {top_pct:.0f}% всех расходов. Это главная статья, Герман заметил 🧐")
    elif top_pct > 25:
        lines.append(f"\n💬 *{top_display}* лидирует ({top_pct:.0f}%). В целом нормально.")
    else:
        lines.append(f"\n💬 Расходы распределены равномерно — всё под контролем 👌")

    # Сравнение с прошлым месяцем
    if "ошибка" not in prev and prev["количество"] > 0:
        diff = total_expense - prev["расходы"]
        prev_name = MONTH_NAMES_RU[prev_m]
        if diff > 500:
            lines.append(f"\n📈 Расходы выросли на *{diff:,.0f} ₽* vs {prev_name}.")
            lines.append("_Герман занервничал, но виду не подаёт_ 😅")
        elif diff < -500:
            lines.append(f"\n📉 Сэкономила *{abs(diff):,.0f} ₽* vs {prev_name}!")
            lines.append("_Герман аплодирует стоя_ 👏")
        else:
            lines.append(f"\n↔️ Примерно как в {prev_name}. Стабильность!")

    # Прогноз — только если ещё не конец месяца (до 25-го)
    import calendar
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_left = days_in_month - now.day
    if now.day <= 25 and now.day > 0:
        daily = total_expense / now.day
        forecast = daily * days_in_month
        lines.append(f"\n🔮 *Прогноз на месяц:* {forecast:,.0f} ₽")
        lines.append(f"_({now.day} дней прошло, темп {daily:,.0f} ₽/день, осталось {days_left} дн.)_")
    elif days_left <= 5:
        lines.append(f"\n📅 До конца месяца {days_left} дн. — почти финиш!")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")
