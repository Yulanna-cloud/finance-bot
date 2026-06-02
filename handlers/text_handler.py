from telegram import Update
from telegram.ext import ContextTypes

from services.sheets_service import write_operation
from services.gemini_service import classify_text

KEYWORDS = ["сколько","покажи","найди","итого","отчет","статистика","всего"]

def is_query(text):
    t = text.lower()
    return "?" in t or any(k in t for k in KEYWORDS)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_query(text):
        res = smart_query(text)
        await update.message.reply_text(res.get("ответ","нет данных"))
        return

    data = classify_text(text)

    ok = write_operation(data)

    if ok:
        msg = f"💰 {data['сумма']} ₽ — {data['категория']}"
        if data["магазин"]:
            msg += f"\n🏪 {data['магазин']}"
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("ошибка записи")
