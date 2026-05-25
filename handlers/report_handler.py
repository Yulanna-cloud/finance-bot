"""
Обработчик команды /отчет.
Читает данные из Google Sheets и формирует красивый отчёт.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import get_monthly_report

logger = logging.getLogger(__name__)


async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Считаю расходы за месяц...")

    try:
        report = get_monthly_report()

        if "ошибка" in report:
            await update.message.reply_text(
                f"❌ Не удалось получить отчёт: {report['ошибка']}"
            )
            return

        month = report["месяц"]
        year = report["год"]
        income = report["доходы"]
        expenses = report["расходы"]
        balance = report["остаток"]
        count = report["количество"]
        top = report["топ_категорий"]

        # Эмодзи для баланса
        balance_emoji = "✅" if balance >= 0 else "🔴"
        balance_sign = "+" if balance >= 0 else ""

        lines = [
            f"📊 *Отчёт за {month} {year}*\n",
            f"💰 Доходы: *{income:,.0f} ₽*",
            f"💸 Расходы: *{expenses:,.0f} ₽*",
            f"{balance_emoji} Остаток: *{balance_sign}{balance:,.0f} ₽*",
            f"🔢 Операций: {count}\n",
        ]

        if top:
            lines.append("📂 *Топ расходов:*")
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (cat, amount) in enumerate(top):
                medal = medals[i] if i < len(medals) else "•"
                pct = (amount / expenses * 100) if expenses > 0 else 0
                lines.append(f"{medal} {cat}: *{amount:,.0f} ₽* ({pct:.0f}%)")

        # Прогноз на следующий месяц
        if expenses > 0 and count > 0:
            from datetime import datetime
            days_in_month = 30
            now = datetime.now()
            days_passed = now.day
            daily_avg = expenses / days_passed if days_passed > 0 else 0
            forecast = daily_avg * days_in_month

            lines.append(f"\n🔮 *Прогноз на месяц:* {forecast:,.0f} ₽")
            lines.append(f"_(в среднем {daily_avg:,.0f} ₽/день)_")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка handle_report: {e}")
        await update.message.reply_text(
            "❌ Не удалось сформировать отчёт. Попробуй позже."
        )
