import logging
from telegram import Update
from telegram.ext import ContextTypes

from services.sheets_service import write_operation, smart_query
from services.gemini_service import classify_text

logger = logging.getLogger(__name__)

QUERY_KEYWORDS = ["сколько","покажи","найди","поиск","отчет","статистика","итого","всего"]

def is_query(text: str) -> bool:
    t = text.lower()
    return "?" in t or any(k in t for k in QUERY_KEYWORDS)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # QUERY
    if is_query(text):
        result = smart_query(text)
        answer = result.get("ответ") or result.get("ошибка") or "Нет данных"
        await update.message.reply_text(answer)
        return

    # CLASSIFY
    data = classify_text(text)

    if not data:
        await update.message.reply_text("Не распознано")
        return

    ok = write_operation(data)

    if ok:
        msg = f"💰 {data.get('сумма')} ₽ — {data.get('категория')}"
        if data.get("магазин"):
            msg += f"\n🏪 {data.get('магазин')}"
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("Ошибка записи")
