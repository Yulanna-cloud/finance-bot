"""
Финансовый Telegram-бот для Юланны
"""
import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from handlers.text_handler import handle_text
from handlers.voice_handler import handle_voice
from handlers.photo_handler import handle_photo, handle_receipt_callback
from handlers.file_handler import handle_file
from handlers.report_handler import handle_report, handle_report_callback
from handlers.archive_handler import handle_archive, handle_smart_query
from handlers.delete_handler import (
    handle_delete, handle_delete_callback,
    handle_restore, handle_restore_callback
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привет! Я твой финансовый помощник 💰\n\n"
        "Что я умею:\n"
        "🎤 *Голосовое сообщение* — скажи «кофе 350» и я запишу\n"
        "📷 *Фото чека* — сфотографируй чек, разберу по позициям\n"
        "📷 *Фото с подписью* — напиши подпись «обучение Маргарите танцы»\n"
        "📄 *Файл выписки* — загрузи PDF/CSV/Excel из банка\n"
        "💬 *Текст* — напиши «такси 300» или «Пятерочка 1200»\n"
        "❓ *Вопрос* — спроси «сколько пришло от Алексея»\n\n"
        "📊 Команды:\n"
        "/otchet — отчёт за месяц\n"
        "/archive — архивировать прошлый месяц\n"
        "/delete — удалить ошибочную запись\n"
        "/restore — восстановить последнее удаление\n"
        "/pomosh — эта справка\n\n"
        "Поехали! 🚀"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN!")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("pomosh", help_command))
    app.add_handler(CommandHandler("otchet", handle_report))
    app.add_handler(CommandHandler("archive", handle_archive))
    app.add_handler(CommandHandler("delete", handle_delete))
    app.add_handler(CommandHandler("restore", handle_restore))

    app.add_handler(CallbackQueryHandler(handle_receipt_callback, pattern="^receipt_"))
    app.add_handler(CallbackQueryHandler(handle_report_callback, pattern="^report_"))
    app.add_handler(CallbackQueryHandler(handle_delete_callback, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(handle_restore_callback, pattern="^restore_"))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
