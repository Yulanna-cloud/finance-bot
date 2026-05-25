"""
Обработчик голосовых сообщений.
Скачивает аудио → Gemini расшифровывает → классифицирует → записывает.
"""

import logging
import io
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import transcribe_voice, classify_text
from services.sheets_service import write_operation

logger = logging.getLogger(__name__)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Слушаю...")

    try:
        # Скачиваем голосовое
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)

        # Читаем байты
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        audio_bytes = buf.getvalue()

        # Расшифровываем через Gemini
        transcribed = transcribe_voice(audio_bytes, mime_type="audio/ogg")

        if not transcribed:
            await update.message.reply_text(
                "🤔 Не смогла расшифровать. Попробуй говорить чуть медленнее или напиши текстом."
            )
            return

        await update.message.reply_text(f"📝 Услышала: _{transcribed}_", parse_mode="Markdown")

        # Классифицируем текст
        result = classify_text(transcribed)

        if not result.get("сумма"):
            await update.message.reply_text(
                "🤔 Не нашла сумму. Скажи, например: *«потратила на кофе триста пятьдесят»*",
                parse_mode="Markdown"
            )
            return

        result["исходный_текст"] = transcribed
        ok = write_operation(result, source="голос")

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
                f"📂 {cat}{subcat_str}{store_str}"
                f"{warning}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ Не удалось записать в таблицу.")

    except Exception as e:
        logger.error(f"Ошибка handle_voice: {e}")
        await update.message.reply_text("❌ Что-то пошло не так с голосовым сообщением.")
