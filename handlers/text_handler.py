
import logging
import re
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import append_rows
from services.llm_service import parse_expense_with_llm

logger = logging.getLogger(__name__)

# Ключевые слова для поиска статистики
QUERY_KEYWORDS = ["сколько", "покажи", "найди", "поиск", "отчет", "статистика", "всего", "итого", "пришло", "потрачено"]

def is_query(text: str) -> bool:
    """
    Проверяет, является ли сообщение вопросом или запросом статистики.
    """
    clean_text = text.lower()
    if "?" in clean_text:
        return True
    return any(keyword in clean_text for keyword in QUERY_KEYWORDS)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Основной обработчик текстовых сообщений.
    """
    text = update.message.text.strip()
    logger.info(f"Получено текстовое сообщение: {text}")

    # 1. ПРОВЕРКА НА ВОПРОС (Перехватываем сразу, чтобы бот не искал сумму)
    if is_query(text):
        logger.info("Сообщение распознано как поисковый запрос.")
        await update.message.reply_chat_action("typing")
        
        result = smart_query(text)
        answer = result.get("ответ") or result.get("ошибка") or "Ничего не нашлось."
        
        await update.message.reply_text(answer, parse_mode="Markdown")
        return

    # 2. ЕСЛИ ЭТО ЗАПИСЬ ОПЕРАЦИИ (Старый рабочий код бота)
    await update.message.reply_chat_action("typing")
    
    # Пытаемся вытащить данные через нейросеть
    expense_data = parse_expense_with_llm(text)
    
    if not expense_data or "error" in expense_data:
        await update.message.reply_text(
            "Не смогла найти сумму в сообщении.\n"
            "Попробуй написать так: `кофе 350` или `такси 300`",
            parse_mode="Markdown"
        )
        return

    # Записываем в таблицу
    success = append_rows(expense_data)
    if success:
        amount = expense_data.get("сумма", 0)
        category = expense_data.get("категория", "Прочее")
        desc = expense_data.get("товар / описание", "")
        
        await update.message.reply_text(
            f"✅ Записала операцию!\n"
            f"💰 Сумма: {amount} ₽\n"
            f"🗂 Категория: {category}\n"
            f"📝 Описание: {desc}"
        )
    else:
        await update.message.reply_text("❌ Не удалось сохранить в Google Таблицу.")
