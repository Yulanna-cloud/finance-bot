"""
Обработчик команды /archive.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import archive_month

logger = logging.getLogger(__name__)


async def handle_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Архивирую прошлый месяц...")
    try:
        result = archive_month()
        if "ошибка" in result:
            await update.message.reply_text(f"❌ Ошибка: {result['ошибка']}")
            return

        lines = [
            f"✅ *{result['месяц']} {result['год']} заархивирован!*\n",
            f"📊 Записей в архиве: {result['записей']}",
            f"💸 Расходы: *{result['расходы']:,.0f} ₽*",
            f"💰 Доходы: *{result['доходы']:,.0f} ₽*",
            f"🗑 Очищено строк: {result['очищено']}",
            f"\n_Таблица ОПЕРАЦИИ очищена и готова к новому месяцу_"
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка archive: {e}")
        await update.message.reply_text("❌ Что-то пошло не так.")


async def handle_smart_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await update.message.reply_text("🔍 Ищу в таблице...")
    try:
        from services.sheets_service import smart_query
        result = smart_query(text)
        if "ошибка" in result:
            await update.message.reply_text(f"❌ {result['ошибка']}")
            return
        await update.message.reply_text(result["ответ"], parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка smart_query: {e}")
        await update.message.reply_text("❌ Не смогла обработать вопрос.")
