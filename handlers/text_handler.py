import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import write_operation as append_rows, smart_query
from services.gemini_service import classify_text as parse_expense_with_llm

logger = logging.getLogger(__name__)

QUERY_KEYWORDS = ["сколько", "покажи", "найди", "поиск", "отчет", "статистика", "всего", "итого", "пришло", "потрачено"]

def is_query(text: str) -> bool:
    clean_text = text.lower()
    if "?" in clean_text:
        return True
    return any(keyword in clean_text for keyword in QUERY_KEYWORDS)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if is_query(text):
        result = smart_query(text)
        answer = result.get("ответ") or result.get("ошибка") or "Ничего не нашлось."
        await update.message.reply_text(answer, parse_mode="Markdown")
        return

    expense_data = parse_expense_with_llm(text)
    
    if not expense_data or "error" in expense_data:
        await update.message.reply_text("Не смогла найти сумму. Напиши: кофе 350")
        return

    success = append_rows(expense_data)
    if success:
        await update.message.reply_text("✅ Записала!")
    else:
        await update.message.reply_text("❌ Ошибка записи.")
