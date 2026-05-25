"""
Обработчик фото чеков.
Скачивает фото → Gemini читает все позиции → каждую записывает отдельно.
"""

import logging
import io
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import read_receipt_image
from services.sheets_service import write_operation, write_operations_batch

logger = logging.getLogger(__name__)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📷 Читаю чек...")

    try:
        # Берём фото в максимальном качестве
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        # Читаем чек через Gemini Vision
        receipt_data = read_receipt_image(image_bytes)

        if "ошибка" in receipt_data:
            await update.message.reply_text(
                f"❌ Не смогла прочитать чек: {receipt_data['ошибка']}\n"
                "Попробуй сфотографировать чётче, с хорошим освещением."
            )
            return

        positions = receipt_data.get("позиции", [])
        store = receipt_data.get("магазин", "")
        total = receipt_data.get("итого", 0)
        date = receipt_data.get("дата")

        if not positions:
            await update.message.reply_text(
                "🤔 Не нашла позиции в чеке. "
                "Попробуй сфотографировать полный чек с позициями."
            )
            return

        # Формируем превью для пользователя
        store_str = f"🏪 *{store}*\n" if store else ""
        lines = [f"📷 Чек прочитан!\n\n{store_str}"]

        operations = []
        for pos in positions:
            name = pos.get("название", "")
            amount = pos.get("сумма", 0)
            cat = pos.get("категория", "Продукты")
            subcat = pos.get("подкатегория", "")
            subcat_str = f" / {subcat}" if subcat else ""

            lines.append(f"• {name} — *{amount} ₽* ({cat}{subcat_str})")

            operations.append({
                "сумма": amount,
                "тип": "расход",
                "категория": cat,
                "подкатегория": subcat,
                "магазин": store,
                "описание": name,
                "дата": date,
                "уверенность": 0.9,
                "исходный_текст": f"чек: {name}",
            })

        if total:
            lines.append(f"\n💰 *Итого: {total} ₽*")

        lines.append(f"\n✅ Записываю {len(operations)} позиций...")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        # Записываем все позиции в таблицу
        ok, errors = write_operations_batch(operations, source="чек")

        result_msg = f"✅ Записано {ok} позиций в таблицу!"
        if errors:
            result_msg += f"\n⚠️ {errors} позиций не записалось — проверь подключение."

        await update.message.reply_text(result_msg)

    except Exception as e:
        logger.error(f"Ошибка handle_photo: {e}")
        await update.message.reply_text(
            "❌ Что-то пошло не так при обработке фото. Попробуй ещё раз."
        )
