"""
Финансовый Telegram-бот для Юланны
Умеет:
- Принимать голосовые сообщения и расшифровывать их
- Принимать фото чеков и читать позиции через Gemini
- Принимать файлы выписок из банка (CSV/Excel)
- Записывать всё в Google Sheets
- Делать отчёт за месяц по команде /отчет
"""

import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from handlers.text_handler import handle_text
from handlers.voice_handler import handle_voice
from handlers.photo_handler import handle_photo
from handlers.file_handler import handle_file
from handlers.report_handler import handle_report

# Настройка логов — будет видно что происходит
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие при первом запуске /start"""
    text = (
        "Привет! Я твой финансовый помощник 💰\n\n"
        "Что я умею:\n"
        "🎤 *Голосовое сообщение* — скажи «кофе 350» и я запишу\n"
        "📷 *Фото чека* — сфотографируй чек, разберу по позициям\n"
        "📄 *Файл выписки* — загрузи CSV/Excel из банка\n"
        "💬 *Текст* — напиши «такси 300» или «Пятерочка 1200»\n\n"
        "📊 Команды:\n"
        "/отчет — отчёт за текущий месяц\n"
        "/помощь — эта справка\n\n"
        "Поехали! 🚀"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN в переменных окружения!")

    app = ApplicationBuilder().token(token).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("помощь", help_command))
    app.add_handler(CommandHandler("отчет", handle_report))
    app.add_handler(CommandHandler("отчёт", handle_report))

    # Сообщения
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
