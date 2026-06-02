import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from handlers.file_handler import handle_file
from handlers.photo_handler import handle_photo
from handlers.voice_handler import handle_voice
from handlers.text_handler import handle_text

from handlers.delete_handler import show_delete_menu, handle_delete_callback

logging.basicConfig(level=logging.INFO)


TOKEN = "8650147262:AAHe9kzNi7GDaPvF1I_uxB5-CjZNOdr7cUo"


async def start(update: Update, context):
    await update.message.reply_text("Бот запущен")


async def delete_command(update: Update, context):
    await show_delete_menu(update, context)


def main():
    app = Application.builder().token(TOKEN).build()

    # текст / файлы / фото / голос
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("delete", delete_command))

    # кнопки удаления (ВАЖНО)
    app.add_handler(CallbackQueryHandler(handle_delete_callback))

    app.run_polling()


if __name__ == "__main__":
    main()
