"""
Финансовый Telegram-бот для Юланны
"""
import os
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from handlers.text_handler import handle_text
from handlers.voice_handler import handle_voice
from handlers.photo_handler import handle_photo, handle_receipt_callback
from services.sheets_service import fix_categories_in_sheet
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


MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🚀 Старт"),           KeyboardButton("❓ Помощь")],
        [KeyboardButton("📊 Отчёт за месяц"), KeyboardButton("🔍 Расшифровать категорию")],
        [KeyboardButton("🗑 Удалить запись"),  KeyboardButton("↩️ Восстановить")],
        [KeyboardButton("📁 Архив")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привет! Я твой финансовый помощник 💰\n\n"
        "Что я умею:\n"
        "🎤 *Голосовое* — скажи «кофе 350» и запишу\n"
        "📷 *Фото чека* — сфотографируй, разберу по позициям\n"
        "📷 *Фото с подписью* — напиши «обучение Маргарите танцы»\n"
        "📄 *Файл выписки* — загрузи PDF/CSV/Excel из банка\n"
        "💬 *Текст* — напиши «такси 300» или «Пятерочка 1200»\n"
        "❓ *Вопрос* — «сколько пришло от Алексея», «расшифруй обучение»\n\n"
        "Кнопки внизу — главное меню 👇"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перехватывает нажатия кнопок главного меню."""
    text = update.message.text.strip()
    if "Старт" in text:
        await start(update, context)
    elif "Отчёт" in text:
        await handle_report(update, context)
    elif "Расшифровать" in text:
        await update.message.reply_text(
            "Напиши название категории, например:\n"
            "_расшифруй обучение_\n_расшифруй переводы_\n_детали продукты_",
            parse_mode="Markdown"
        )
    elif "Удалить" in text:
        await handle_delete(update, context)
    elif "Восстановить" in text:
        await handle_restore(update, context)
    elif "Архив" in text:
        await handle_archive(update, context)
    elif "Помощь" in text:
        await help_command(update, context)


async def fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔧 Исправляю категории в таблице...")
    result = fix_categories_in_sheet()
    if "ошибка" in result:
        await update.message.reply_text(f"❌ Ошибка: {result['ошибка']}")
    else:
        await update.message.reply_text(f"✅ Готово! Исправлено записей: {result['исправлено']}")


MENU_BUTTON_TEXTS = ["🚀 Старт", "📊 Отчёт", "🔍 Расшифровать", "🗑 Удалить", "↩️ Восстановить", "📁 Архив", "❓ Помощь"]


async def post_init(app):
    """Регистрирует список команд в Telegram (меню через '/')."""
    await app.bot.set_my_commands([
        BotCommand("start",   "🚀 Запустить бота / главное меню"),
        BotCommand("otchet",  "📊 Отчёт за месяц"),
        BotCommand("delete",  "🗑 Удалить ошибочную запись"),
        BotCommand("restore", "↩️ Восстановить удалённое"),
        BotCommand("archive", "📁 Архивировать прошлый месяц"),
        BotCommand("fix",     "🔧 Исправить категории в таблице"),
        BotCommand("pomosh",  "❓ Помощь"),
    ])


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN!")

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("pomosh", help_command))
    app.add_handler(CommandHandler("otchet", handle_report))
    app.add_handler(CommandHandler("archive", handle_archive))
    app.add_handler(CommandHandler("delete", handle_delete))
    app.add_handler(CommandHandler("restore", handle_restore))
    app.add_handler(CommandHandler("fix", fix_command))

    app.add_handler(CallbackQueryHandler(handle_receipt_callback, pattern="^receipt_"))
    app.add_handler(CallbackQueryHandler(handle_report_callback, pattern="^report_"))
    app.add_handler(CallbackQueryHandler(handle_delete_callback, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(handle_restore_callback, pattern="^restore_"))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    # Кнопки меню обрабатываем РАНЬШЕ обычного текста
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("^(" + "|".join(MENU_BUTTON_TEXTS) + ")"),
        handle_menu_button
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
