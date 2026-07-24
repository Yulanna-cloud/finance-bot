"""
Финансовый Telegram-бот для Юланны — режим POLLING + самопинг
"""
import os
import logging
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import time as datetime_time
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
from handlers.year_handler import handle_year, handle_analiz
from handlers.edit_handler import handle_edit, handle_edit_callback, handle_edit_text
from handlers.budget_handler import handle_budget
from handlers.plan_handler import handle_plan, handle_plan_callback, handle_plan_text, monthly_plan_reminder
from handlers.delete_handler import (
    handle_delete, handle_delete_callback,
    handle_restore, handle_restore_callback
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("☰ Меню")]],
    resize_keyboard=True,
    is_persistent=True,
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Отчёт за месяц"), KeyboardButton("📅 Итоги года")],
        [KeyboardButton("🔍 Расшифровать категорию"), KeyboardButton("🧠 Анализ трат")],
        [KeyboardButton("📋 Планирование"),     KeyboardButton("💼 Бюджет")],
        [KeyboardButton("🗑 Удалить запись"),  KeyboardButton("📁 Архив")],
        [KeyboardButton("✏️ Изменить запись"), KeyboardButton("↩️ Восстановить")],
        [KeyboardButton("❓ Помощь"),           KeyboardButton("✖️ Закрыть меню")],
    ],
    resize_keyboard=True,
    is_persistent=False,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привет! Я Герман — твой финансовый помощник 💰\n\n"
        "Слежу за деньгами, пока ты тратишь. Такая у меня судьба 😄\n\n"
        "Что умею:\n"
        "🎤 *Голосовое* — скажи «кофе 350», запишу не моргнув\n"
        "📷 *Фото чека* — скинь чек, разберу по позициям\n"
        "📷 *Фото с подписью* — добавь «обучение Рите танцы» и я всё пойму\n"
        "📄 *Файл выписки* — загрузи PDF/CSV/Excel из банка\n"
        "💬 *Текст* — «такси 300» или «Пятёрочка 1200», как удобно\n"
        "❓ *Вопрос* — «сколько перевела Рите», «расшифруй красота»\n\n"
        "Нажми *☰ Меню* чтобы открыть все функции 👇"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "☰ Меню" in text:
        await update.message.reply_text("Выбери что нужно:", reply_markup=MAIN_KEYBOARD)
    elif "Закрыть меню" in text:
        await update.message.reply_text("Хорошо, убрал 👌", reply_markup=MIN_KEYBOARD)
    elif "Отчёт" in text:
        await handle_report(update, context)
    elif "Итоги года" in text:
        await handle_year(update, context)
    elif "Анализ трат" in text:
        await handle_analiz(update, context)
    elif "Расшифровать" in text:
        await update.message.reply_text(
            "Напиши название категории, например:\n"
            "_расшифруй обучение_\n_расшифруй переводы_\n_детали продукты_",
            parse_mode="Markdown"
        )
    elif "Планирование" in text:
        await handle_plan(update, context)
    elif "Бюджет" in text:
        await handle_budget(update, context)
    elif "Удалить" in text:
        await handle_delete(update, context)
    elif "Изменить" in text:
        await handle_edit(update, context)
    elif "Восстановить" in text:
        await handle_restore(update, context)
    elif "Архив" in text:
        await handle_archive(update, context)
    elif "Помощь" in text:
        await help_command(update, context)


async def fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔧 Герман на хозяйстве — навожу порядок в категориях...")
    result = fix_categories_in_sheet()
    if "ошибка" in result:
        await update.message.reply_text(f"❌ Что-то пошло не так: {result['ошибка']}")
    else:
        n = result['исправлено']
        if n == 0:
            await update.message.reply_text("✅ Проверил всё — и так чисто, исправлять нечего 👌")
        else:
            await update.message.reply_text(f"✅ Готово! Поправил {n} записей — теперь всё по полочкам 📂")


MENU_BUTTON_TEXTS = ["☰ Меню", "✖️ Закрыть", "📊 Отчёт", "📅 Итоги", "🧠 Анализ",
                     "📋 Планирование", "💼 Бюджет", "🔍 Расшифровать", "🗑 Удалить",
                     "✏️ Изменить", "↩️ Восстановить", "📁 Архив", "❓ Помощь"]


async def monthly_reminder(context):
    chat_id = context.job.data
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "📅 Привет! Герман напоминает — месяц заканчивается.\n"
            "Самое время взглянуть на отчёт и понять, куда утекли деньги 😄\n\n"
            "Нажми 📊 *Отчёт за месяц* или /otchet"
        ),
        parse_mode="Markdown"
    )


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",   "🚀 Запустить бота / главное меню"),
        BotCommand("otchet",  "📊 Отчёт за месяц"),
        BotCommand("year",    "📅 Итоги года"),
        BotCommand("analiz",  "🧠 Анализ трат"),
        BotCommand("delete",  "🗑 Удалить ошибочную запись"),
        BotCommand("restore", "↩️ Восстановить удалённое"),
        BotCommand("archive", "📁 Архивировать прошлый месяц"),
        BotCommand("edit",    "✏️ Изменить запись"),
        BotCommand("budget",  "💼 Бюджет по категориям"),
        BotCommand("plan",    "📋 Планирование бюджета на месяц"),
        BotCommand("fix",     "🔧 Исправить категории в таблице"),
        BotCommand("pomosh",  "❓ Помощь"),
    ])

    chat_id = os.environ.get("OWNER_CHAT_ID")
    if chat_id:
        app.job_queue.run_monthly(
            monthly_reminder,
            when=datetime_time(hour=13, minute=0),
            day=28,
            data=int(chat_id),
            name="monthly_reminder"
        )
        app.job_queue.run_monthly(
            monthly_plan_reminder,
            when=datetime_time(hour=9, minute=0),
            day=1,
            data=int(chat_id),
            name="monthly_plan_reminder"
        )


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), _HealthHandler).serve_forever()


def self_ping_loop():
    """Пингует себя каждые 4 минуты — Render не засыпает."""
    import time
    port = int(os.environ.get("PORT", 8080))
    url = f"http://localhost:{port}/"
    time.sleep(30)
    while True:
        try:
            urllib.request.urlopen(url, timeout=5)
        except Exception:
            pass
        time.sleep(240)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN!")

    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("pomosh", help_command))
    app.add_handler(CommandHandler("otchet", handle_report))
    app.add_handler(CommandHandler("archive", handle_archive))
    app.add_handler(CommandHandler("delete", handle_delete))
    app.add_handler(CommandHandler("restore", handle_restore))
    app.add_handler(CommandHandler("fix", fix_command))
    app.add_handler(CommandHandler("year", handle_year))
    app.add_handler(CommandHandler("analiz", handle_analiz))
    app.add_handler(CommandHandler("edit", handle_edit))
    app.add_handler(CommandHandler("budget", handle_budget))
    app.add_handler(CommandHandler("plan", handle_plan))

    app.add_handler(CallbackQueryHandler(handle_receipt_callback, pattern="^receipt_"))
    app.add_handler(CallbackQueryHandler(handle_edit_callback, pattern="^edit_"))
    app.add_handler(CallbackQueryHandler(handle_plan_callback, pattern="^plan"))
    app.add_handler(CallbackQueryHandler(handle_report_callback, pattern="^report_"))
    app.add_handler(CallbackQueryHandler(handle_delete_callback, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(handle_restore_callback, pattern="^restore_"))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("^(" + "|".join(MENU_BUTTON_TEXTS) + ")"),
        handle_menu_button
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
