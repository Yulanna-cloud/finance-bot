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
    total = cur["расходы"]
    if not cats or total == 0:
        await msg.reply_text("Расходов пока нет. Герман одобряет такой подход 👍")
        return

    # Топ-3 категории
    top3 = sorted(cats.items(), key=lambda x: x[1], reverse=True)[:3]

    lines = [f"🧠 *Анализ трат — {MONTH_NAMES_RU[now.month]}*\n"]

    lines.append("🔝 *Топ расходов:*")
    for i, (cat, amount) in enumerate(top3, 1):
        pct = amount / total * 100
        lines.append(f"  {i}. {cat}: *{amount:,.0f} ₽* ({pct:.0f}%)")

    # Комментарий по топ-1
    top_cat, top_amount = top3[0]
    top_pct = top_amount / total * 100
    if top_pct > 40:
        lines.append(f"\n💬 *{top_cat}* съедает {top_pct:.0f}% всех расходов. Это очень много — Герман смотрит с подозрением 🧐")
    elif top_pct > 25:
        lines.append(f"\n💬 *{top_cat}* — главная статья расходов ({top_pct:.0f}%). В целом нормально.")
    else:
        lines.append(f"\n💬 Расходы распределены равномерно. Герман доволен — всё под контролем 👌")

    # Сравнение с прошлым месяцем
    if "ошибка" not in prev and prev["количество"] > 0:
        diff = total - prev["расходы"]
        prev_name = MONTH_NAMES_RU[prev_m]
        if diff > 500:
            lines.append(f"\n📈 По сравнению с *{prev_name}* потратила на *{diff:,.0f} ₽* больше.")
            lines.append("_Герман занервничал, но виду не подаёт_ 😅")
        elif diff < -500:
            lines.append(f"\n📉 По сравнению с *{prev_name}* сэкономила *{abs(diff):,.0f} ₽*!")
            lines.append("_Герман аплодирует стоя_ 👏")
        else:
            lines.append(f"\n↔️ По сравнению с *{prev_name}* — примерно столько же. Стабильность!")

    # Прогноз
    if now.day > 0:
        daily = total / now.day
        forecast = daily * 30
        lines.append(f"\n🔮 *Прогноз до конца месяца:* {forecast:,.0f} ₽")
        lines.append(f"_(темп: {daily:,.0f} ₽/день)_")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")
