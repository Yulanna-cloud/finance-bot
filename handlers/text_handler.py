import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import write_operation, smart_query
from services.gemini_service import classify_text

logger = logging.getLogger(__name__)

QUERY_KEYWORDS = ["сколько", "покажи", "найди", "поиск", "отчет", "статистика", "всего", "итого", "пришло", "потрачено"]

FAMILY_NAMES = {
    "маргарита": "Маргарита П.",
    "диана":     "Диана Ш.",
    "алексей":   "Алексей П.",
    "райса":     "Райса Г.",
    "юланна":    "Юланна Г.",
    "салават":   "Салават Г.",
    "дамир":     "Дамир Г.",
    "ольга":     "Ольга Г.",
}

def extract_sender(text: str) -> str:
    """Ищет имя отправителя в тексте прихода."""
    t = text.lower()
    for key, full_name in FAMILY_NAMES.items():
        if key in t:
            return full_name
    return ""

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

    expense_data = classify_text(text)

    if not expense_data or "error" in expense_data:
        await update.message.reply_text("Не смогла найти сумму. Напиши: кофе 350")
        return

    # Если это доход — пробуем найти отправителя
    if expense_data.get("тип") == "доход":
        sender = extract_sender(text)
        if sender:
            expense_data["отправитель"] = sender

    expense_data.setdefault("отправитель", "")
    expense_data.setdefault("получатель", "")

    success = write_operation(expense_data)
    if success:
        тип = expense_data.get("тип", "расход")
        сумма = expense_data.get("сумма", "")
        кат = expense_data.get("категория", "")
        отправитель = expense_data.get("отправитель", "")
        emoji = "💰" if тип == "доход" else "💸"
        sender_str = f"\n👤 От: {отправитель}" if отправитель else ""
        await update.message.reply_text(
            f"{emoji} Записала!\n💰 *{сумма} ₽* — {кат}{sender_str}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Ошибка записи.")
