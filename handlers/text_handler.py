import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import write_operation, smart_query
from services.gemini_service import classify_text

logger = logging.getLogger(__name__)

QUERY_KEYWORDS = [
    "сколько", "покажи", "найди", "поиск", "отчет", "статистика",
    "всего", "итого", "пришло", "потрачено", "маргарит", "диан",
    "алекс", "расход", "доход"
]

def is_query(text: str) -> bool:
    t = text.lower()
    if "?" in t:
        return True
    return any(k in t for k in QUERY_KEYWORDS)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_query(text):
        result = smart_query(text)
        answer = result.get("ответ") or result.get("ошибка") or "Ничего не нашлось."
        await update.message.reply_text(answer, parse_mode="Markdown")
        return

    data = classify_text(text)

    if not data or not data.get("сумма"):
        await update.message.reply_text("Не смогла найти сумму. Напиши: кофе 350")
        return

    ok = write_operation(data)
    if ok:
        тип = data.get("тип", "расход")
        emoji = "💰" if тип == "доход" else "💸"
        msg = f"{emoji} *{data['сумма']} ₽* — {data.get('категория', '')}"
        if data.get("подкатегория"):
            msg += f" / {data['подкатегория']}"
        if data.get("магазин"):
            msg += f"\n🏪 {data['магазин']}"
        if data.get("получатель"):
            msg += f"\n👤 Получатель: {data['получатель']}"
        if data.get("отправитель"):
            msg += f"\n👤 От: {data['отправитель']}"
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Ошибка записи.")
