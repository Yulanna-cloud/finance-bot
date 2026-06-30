"""
Обработчик команды /отчет.
Читает данные из Google Sheets и формирует красивый отчёт.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_monthly_report, now_ufa, MONTH_NAMES_RU

logger = logging.getLogger(__name__)


def build_report_keyboard() -> InlineKeyboardMarkup:
    """Строит клавиатуру выбора периода."""
    now = now_ufa()

    cur_month = now.month
    cur_year = now.year

    if now.month == 1:
        prev_month = 12
        prev_year = cur_year - 1
    else:
        prev_month = cur_month - 1
        prev_year = cur_year

    cur_name = MONTH_NAMES_RU[cur_month]
    prev_name = MONTH_NAMES_RU[prev_month]

    keyboard = [
        [InlineKeyboardButton(
            f"📅 {cur_name} {cur_year} (текущий)",
            callback_data=f"report_{cur_month}_{cur_year}"
        )],
        [InlineKeyboardButton(
            f"📅 {prev_name} {prev_year} (прошлый)",
            callback_data=f"report_{prev_month}_{prev_year}"
        )],
        [InlineKeyboardButton(
            "🗓 Другой период...",
            callback_data="report_pick"
        )],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_month_keyboard(year: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру выбора месяца."""
    now = now_ufa()
    buttons = []
    row = []
    for m in range(1, 13):
        # Не показываем будущие месяцы
        if year == now.year and m > now.month:
            continue
        name = MONTH_NAMES_RU[m][:3]  # Сокр. название: Янв, Фев...
        row.append(InlineKeyboardButton(name, callback_data=f"report_{m}_{year}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Кнопки переключения года
    nav = []
    if year > now.year - 2:
        nav.append(InlineKeyboardButton("◀ " + str(year - 1), callback_data=f"report_year_{year - 1}"))
    nav.append(InlineKeyboardButton("❌ Отмена", callback_data="report_cancel"))
    if year < now.year:
        nav.append(InlineKeyboardButton(str(year + 1) + " ▶", callback_data=f"report_year_{year + 1}"))
    buttons.append(nav)

    return InlineKeyboardMarkup(buttons)


async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора периода."""
    await update.message.reply_text(
        "📊 За какой период показать отчёт?",
        reply_markup=build_report_keyboard()
    )


async def handle_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия кнопок отчёта."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # Выбор года для детального меню
    if data.startswith("report_year_"):
        year = int(data.replace("report_year_", ""))
        await query.edit_message_text(
            f"📅 Выбери месяц ({year}):",
            reply_markup=build_month_keyboard(year)
        )
        return

    # Открыть выбор месяца
    if data == "report_pick":
        now = now_ufa()
        await query.edit_message_text(
            f"📅 Выбери месяц ({now.year}):",
            reply_markup=build_month_keyboard(now.year)
        )
        return

    # Отмена
    if data == "report_cancel":
        await query.edit_message_text("Отменено.")
        return

    # Конкретный месяц: report_5_2026
    if data.startswith("report_"):
        parts = data.split("_")
        if len(parts) == 3:
            target_month = int(parts[1])
            target_year = int(parts[2])
            await query.edit_message_text(
                f"📊 Считаю расходы за {MONTH_NAMES_RU[target_month]} {target_year}..."
            )
            await _send_report(query, target_month, target_year)


async def _send_report(query, target_month: int, target_year: int):
    """Получает данные и отправляет отчёт."""
    try:
        now = now_ufa()
        report = get_monthly_report(month=target_month, year=target_year)

        if "ошибка" in report:
            await query.edit_message_text(f"❌ Ошибка: {report['ошибка']}")
            return

        month = report["месяц"]
        year = report["год"]
        income = report["доходы"]
        expenses = report["расходы"]
        balance = report["остаток"]
        count = report["количество"]
        all_cats = report.get("все_категории", {})

        balance_emoji = "✅" if balance >= 0 else "🔴"
        balance_sign = "+" if balance >= 0 else ""

        lines = [
            f"📊 *Отчёт за {month} {year}*\n",
            f"💰 Доходы: *{income:,.0f} ₽*",
            f"💸 Расходы: *{expenses:,.0f} ₽*",
            f"{balance_emoji} Остаток: *{balance_sign}{balance:,.0f} ₽*",
            f"🔢 Операций: {count}\n",
        ]

        transfers_detail = report.get("переводы_детали", {})

        if all_cats:
            lines.append("📂 *Расходы по категориям:*")
            medals = ["🥇", "🥈", "🥉"]
            sorted_cats = sorted(all_cats.items(), key=lambda x: x[1], reverse=True)
            for i, (cat, amount) in enumerate(sorted_cats):
                medal = medals[i] if i < len(medals) else "•"
                pct = (amount / expenses * 100) if expenses > 0 else 0
                lines.append(f"{medal} {cat}: *{amount:,.0f} ₽* ({pct:.0f}%)")
                if cat == "Переводы" and transfers_detail:
                    for recv, sum_ in sorted(transfers_detail.items(), key=lambda x: x[1], reverse=True):
                        lines.append(f"  └ {recv}: {sum_:,.0f} ₽")

        # Прогноз только для текущего месяца
        if target_month == now.month and target_year == now.year:
            if expenses > 0 and count > 0:
                days_passed = now.day
                daily_avg = expenses / days_passed if days_passed > 0 else 0
                forecast = daily_avg * 30
                lines.append(f"\n🔮 *Прогноз на месяц:* {forecast:,.0f} ₽")
                lines.append(f"_(в среднем {daily_avg:,.0f} ₽/день)_")

        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка _send_report: {e}")
        await query.edit_message_text("❌ Не удалось сформировать отчёт.")
