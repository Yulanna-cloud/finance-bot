"""
Обработчик фото чеков.
Читает чек → группирует по категориям → записывает суммарно.
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
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        receipt_data = read_receipt_image(image_bytes)

        if "ошибка" in receipt_data:
            await update.message.reply_text(
                f"❌ Не смогла прочитать чек: {receipt_data['ошибка']}\n"
                "Попробуй сфотографировать чётче, с хорошим освещением."
            )
            return

        store = receipt_data.get("магазин", "")
        total = receipt_data.get("итого", 0)
        date = receipt_data.get("дата")

        # Новый формат — категории с суммами
        categories = receipt_data.get("категории", [])

        # Запасной вариант — старый формат с позициями
        if not categories:
            positions = receipt_data.get("позиции", [])
            if positions:
                # Группируем позиции по категориям
                cat_sums = {}
                for pos in positions:
                    cat = pos.get("категория", "Продукты")
                    cat_sums[cat] = cat_sums.get(cat, 0) + float(pos.get("сумма", 0))
                categories = [{"категория": k, "сумма": v} for k, v in cat_sums.items()]

        if not categories:
            await update.message.reply_text(
                "🤔 Не нашла позиции в чеке. "
                "Попробуй сфотографировать полный чек с позициями."
            )
            return

        store_str = f"🏪 *{store}*\n" if store else ""
        lines = [f"📷 Чек прочитан!\n\n{store_str}"]

        operations = []
        for item in categories:
            cat = item.get("категория", "Прочее")
            amount = float(item.get("сумма", 0))
            if amount <= 0:
                continue

            lines.append(f"• {cat} — *{amount:.0f} ₽*")

            operations.append({
                "сумма": amount,
                "тип": "расход",
                "категория": cat,
                "подкатегория": "",
                "магазин": store,
                "описание": f"{store} / {cat}" if store else cat,
                "дата": date,
                "уверенность": 0.9,
                "исходный_текст": f"чек: {cat}",
            })

        if total:
            lines.append(f"\n💰 *Итого: {total:.0f} ₽*")
        lines.append(f"\n✅ Записываю {len(operations)} позиций...")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        ok, errors = write_operations_batch(operations, source="чек")

        result_msg = f"✅ Записано {ok} категорий в таблицу!"
        if errors:
            result_msg += f"\n⚠️ {errors} позиций не записалось — проверь подключение."

        await update.message.reply_text(result_msg)

    except Exception as e:
        logger.error(f"Ошибка handle_photo: {e}")
        await update.message.reply_text(
            "❌ Что-то пошло не так при обработке фото. Попробуй ещё раз."
        )
