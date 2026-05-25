"""
Обработчик текстовых сообщений.
Примеры: "кофе 350", "такси 300", "Пятерочка 1450", "зарплата 85000"
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import classify_text
from services.sheets_service import write_operation

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    await update.message.reply_text("⏳ Записываю...")

    try:
        result = classify_text(text)

        if not result.get("сумма"):
            await update.message.reply_text(
                "🤔 Не смогла найти сумму в сообщении.\n"
                "Попробуй написать так: *кофе 350* или *такси 300 рублей*",
                parse_mode="Markdown"
            )
            return

        result["исходный_текст"] = text
        ok = write_operation(result, source="telegram_текст")

        if ok:
            emoji = "💸" if result.get("тип") == "расход" else "💰"
            cat = result.get("категория", "Прочее")
            subcat = result.get("подкатегория", "")
            subcat_str = f" / {subcat}" if subcat else ""
            store = result.get("магазин", "")
            store_str = f"\n🏪 {store}" if store else ""
            confidence = result.get("уверенность", 0)
            warning = "\n⚠️ _Низкая уверенность — проверь в таблице_" if confidence < 0.8 else ""

            await update.message.reply_text(
                f"{emoji} Записано!\n\n"
                f"💰 *{result['сумма']} ₽*\n"
                f"📂 {cat}{subcat_str}{store_str}\n"
                f"📝 {result.get('описание', text)}"
                f"{warning}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ Не удалось записать в таблицу. Проверь настройки Google Sheets."
            )

    except Exception as e:
        logger.error(f"Ошибка handle_text: {e}")
        await update.message.reply_text(
            "❌ Что-то пошло не так. Попробуй ещё раз."
        )
