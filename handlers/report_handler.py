"""
Обработчик команды /отчет.
Читает данные из Google Sheets и формирует красивый отчёт.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.sheets_service import get_monthly_report, get_year_summary, now_ufa, MONTH_NAMES_RU, get_sheets_client, SPREADSHEET_ID
from services.report_image import render_report_image

logger = logging.getLogger(__name__)


def _build_category_lines(all_cats: dict, expenses: float,
                          transfers_detail: dict, clothing_detail: dict) -> list:
    lines = ["📂 *Расходы по категориям:*\n"]
    GROUPS = [
        ("🏠 Жильё и платежи",  ["Ипотека", "ипотека", "Коммуналка", "Интернет", "Связь", "Страховка", "Жилье"]),
        ("🍎 Еда",              ["Продукты", "Кафе"]),
        ("🧹 Быт",              ["Бытовая химия", "Хозтовары"]),
        ("💄 Красота",          ["Красота"]),
        ("👗 Одежда",           ["Одежда", "Дети"]),
        ("🍷 Алкоголь и табак", ["Алкоголь", "Табак"]),
        ("💊 Здоровье",         ["Медицина", "Аптека"]),
        ("📚 Развитие",         ["Обучение", "Подписки ИИ"]),
        ("🎮 Досуг",            ["Развлечения", "Подписки"]),
        ("🐾 Животные",         ["Животные"]),
        ("🚗 Транспорт",        ["Транспорт"]),
        ("💸 Переводы",         ["Переводы"]),
        ("📦 Прочее",           ["Электротовары", "Бытовая техника", "Прочее"]),
    ]
    NAME_SHORT = {"Маргарита П.": "Рите", "Диана Ш.": "Диане", "Алексей П.": "Лёше"}
    shown = set()
    for group_name, group_cats in GROUPS:
        group_lines = []
        for cat in group_cats:
            matched = next((k for k in all_cats if k.lower() == cat.lower()), None)
            if matched and matched not in shown:
                amount = all_cats[matched]
                pct = (amount / expenses * 100) if expenses > 0 else 0
                display = matched[0].upper() + matched[1:] if matched else matched
                group_lines.append(f"  • {display}: *{amount:,.0f} ₽* ({pct:.0f}%)")
                shown.add(matched)
                if matched.lower() == "переводы" and transfers_detail:
                    for recv, sum_ in sorted(transfers_detail.items(), key=lambda x: x[1], reverse=True):
                        group_lines.append(f"    └ {recv}: {sum_:,.0f} ₽")
                if matched.lower() == "одежда" and clothing_detail:
                    for person, sum_ in sorted(clothing_detail.items(), key=lambda x: x[1], reverse=True):
                        label = NAME_SHORT.get(person, person)
                        group_lines.append(f"    └ Одежда {label}: {sum_:,.0f} ₽")
        if group_lines:
            lines.append(f"*{group_name}*")
            lines.extend(group_lines)
            lines.append("")
    leftover = [(k, v) for k, v in all_cats.items() if k not in shown]
    if leftover:
        lines.append("*📦 Остальное*")
        for cat, amount in sorted(leftover, key=lambda x: x[1], reverse=True):
            pct = (amount / expenses * 100) if expenses > 0 else 0
            lines.append(f"  • {cat}: *{amount:,.0f} ₽* ({pct:.0f}%)")
    return lines


def build_report_keyboard() -> InlineKeyboardMarkup:
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
    now = now_ufa()
    buttons = []
    row = []
    for m in range(1, 13):
        if year == now.year and m > now.month:
            continue
        name = MONTH_NAMES_RU[m][:3]
        row.append(InlineKeyboardButton(name, callback_data=f"report_{m}_{year}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    nav = []
    if year > now.year - 2:
        nav.append(InlineKeyboardButton("◀ " + str(year - 1), callback_data=f"report_year_{year - 1}"))
    nav.append(InlineKeyboardButton("❌ Отмена", callback_data="report_cancel"))
    if year < now.year:
        nav.append(InlineKeyboardButton(str(year + 1) + " ▶", callback_data=f"report_year_{year + 1}"))
    buttons.append(nav)
    return InlineKeyboardMarkup(buttons)


async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 За какой период показать отчёт?",
        reply_markup=build_report_keyboard()
    )


async def handle_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("report_year_"):
        year = int(data.replace("report_year_", ""))
        await query.edit_message_text(
            f"📅 Выбери месяц ({year}):",
            reply_markup=build_month_keyboard(year)
        )
        return

    if data == "report_pick":
        now = now_ufa()
        await query.edit_message_text(
            f"📅 Выбери месяц ({now.year}):",
            reply_markup=build_month_keyboard(now.year)
        )
        return

    if data == "report_cancel":
        await query.edit_message_text("Отменено.")
        return

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
        transfers_detail = report.get("переводы_детали", {})
        clothing_detail  = report.get("одежда_детали", {})

        balance_emoji = "✅" if balance >= 0 else "🔴"
        balance_sign = "+" if balance >= 0 else ""

        prev_m = target_month - 1 if target_month > 1 else 12
        prev_y = target_year if target_month > 1 else target_year - 1
        prev = get_monthly_report(month=prev_m, year=prev_y)
        prev_ok = "ошибка" not in prev and prev.get("количество", 0) > 0
        prev_expenses = prev["расходы"] if prev_ok else None
        prev_name = MONTH_NAMES_RU[prev_m]

        summary = [
            f"📊 *Отчёт за {month} {year}*\n",
            f"💰 Доходы: *{income:,.0f} ₽*",
            f"💸 Расходы: *{expenses:,.0f} ₽*",
            f"{balance_emoji} Остаток: *{balance_sign}{balance:,.0f} ₽*",
        ]
        if report.get("архив"):
            summary.append("🗄 _из архива (сводка по категориям)_")
        else:
            summary.append(f"🔢 Операций: {count}")
        if target_month == now.month and target_year == now.year and expenses > 0 and count > 0:
            daily_avg = expenses / now.day if now.day > 0 else 0
            summary.append(f"\n🔮 *Прогноз на месяц:* {daily_avg * 30:,.0f} ₽ _(≈{daily_avg:,.0f} ₽/день)_")

        image = None
        if all_cats:
            image = render_report_image(
                month, year, income, expenses, balance, all_cats,
                prev_expenses=prev_expenses, prev_month_name=prev_name,
            )

        if image is not None:
            await query.edit_message_text("\n".join(summary), parse_mode="Markdown")
            await query.message.reply_photo(photo=image)
            return

        lines = summary[:]
        lines.append("")
        if all_cats:
            lines.extend(_build_category_lines(all_cats, expenses, transfers_detail, clothing_detail))
        if prev_ok:
            diff = expenses - prev["расходы"]
            diff_sign = "+" if diff >= 0 else "−"
            diff_emoji = "📈" if diff > 0 else "📉"
            lines.append(f"\n{diff_emoji} *Против {prev_name}:* {diff_sign}{abs(diff):,.0f} ₽")
            if diff > 0:
                lines.append("_Потратила больше, чем в прошлом месяце. Герман молчит, но заметил 😏_")
            elif diff < 0:
                lines.append("_Потратила меньше! Герман доволен 👍_")
            else:
                lines.append("_Копейка в копейку с прошлым месяцем. Редкость!_")

        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка _send_report: {e}", exc_info=True)
        await query.edit_message_text("❌ Не удалось сформировать отчёт.")


# ====== НОВОЕ: команда /balans ======

def get_total_balance() -> dict:
    """
    Считает реальный баланс за всё время:
    сумма всех доходов минус сумма всех расходов из архива + текущий месяц.
    """
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)

        total_income = 0.0
        total_expense = 0.0
        months_count = 0

        # Читаем архив
        try:
            archive_sheet = spreadsheet.worksheet("АРХИВ")
            archive_rows = archive_sheet.get_all_values()
            if len(archive_rows) > 1:
                headers = [h.strip().lower() for h in archive_rows[0]]
                def col(names, default):
                    for n in names:
                        for i, h in enumerate(headers):
                            if n in h:
                                return i
                    return default
                ic_type = col(["тип"], 4)
                ic_sum  = col(["сумма"], 5)
                ic_period = col(["период", "месяц"], 2)

                seen_periods = set()
                for row in archive_rows[1:]:
                    period = row[ic_period].strip() if ic_period < len(row) else ""
                    if period:
                        seen_periods.add(period)
                    try:
                        amount = float(
                            row[ic_sum].replace(" ", "").replace("\xa0", "").replace(",", ".")
                        ) if ic_sum < len(row) else 0
                    except (ValueError, TypeError):
                        continue
                    if amount <= 0:
                        continue
                    row_type = row[ic_type].strip().lower() if ic_type < len(row) else ""
                    if row_type == "доход":
                        total_income += amount
                    else:
                        total_expense += amount
                months_count = len(seen_periods)
        except Exception as e:
            logger.warning(f"Архив недоступен: {e}")

        # Читаем текущий месяц
        now = now_ufa()
        current = get_monthly_report(month=now.month, year=now.year)
        if "ошибка" not in current:
            total_income += current.get("доходы", 0)
            total_expense += current.get("расходы", 0)
            if current.get("количество", 0) > 0:
                months_count += 1

        balance = total_income - total_expense

        return {
            "баланс": balance,
            "доходы_всего": total_income,
            "расходы_всего": total_expense,
            "месяцев": months_count,
            "текущий_месяц": MONTH_NAMES_RU[now.month],
            "текущий_год": now.year,
        }

    except Exception as e:
        logger.error(f"Ошибка get_total_balance: {e}")
        return {"ошибка": str(e)}


async def handle_balans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /balans — показывает реальный баланс за всё время."""
    await update.message.reply_text("💳 Считаю реальный баланс...")
    try:
        result = get_total_balance()

        if "ошибка" in result:
            await update.message.reply_text(f"❌ Ошибка: {result['ошибка']}")
            return

        balance = result["баланс"]
        income = result["доходы_всего"]
        expense = result["расходы_всего"]
        months = result["месяцев"]
        cur_month = result["текущий_месяц"]
        cur_year = result["текущий_год"]

        balance_emoji = "✅" if balance >= 0 else "🔴"
        balance_sign = "+" if balance >= 0 else ""

        lines = [
            f"💳 *Реальный баланс на {cur_month} {cur_year}*\n",
            f"💰 Всего доходов: *{income:,.0f} ₽*",
            f"💸 Всего расходов: *{expense:,.0f} ₽*",
            f"{balance_emoji} *Остаток: {balance_sign}{balance:,.0f} ₽*",
            f"\n🗓 Учтено месяцев: {months}",
            f"\n_Включает архив + {cur_month} {cur_year}_",
        ]

        if balance < 0:
            lines.append("\n⚠️ _Расходы превышают доходы. Возможно, не все доходы внесены._")
            lines.append("_Запишите начальный баланс: напишите боту «начальный баланс 50000»_")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка handle_balans: {e}")
        await update.message.reply_text("❌ Не удалось посчитать баланс.")

# ====================================
