"""
Обработчик текстовых сообщений.
Примеры: "кофе 350", "такси 300", "Пятерочка 1450", "зарплата 85000"
"""
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import classify_text
from services.sheets_service import write_operation

logger = logging.getLogger(__name__)


def extract_amount(text: str):
    patterns = [
        r'(\d[\d\s]*[\d])[,.](\d{2})\b',
        r'\b(\d[\d\s]{0,6}\d)\b',
        r'\b(\d+)\b',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            raw = matches[-1] if isinstance(matches[-1], str) else matches[-1][0]
            clean = raw.replace(" ", "").replace(",", ".")
            try:
                return float(clean)
            except ValueError:
                continue
    return None


async def _send_multi(update, result, source):
    """Записывает несколько позиций из детального ввода."""
    позиции = result.get("позиции", [])
    магазин = result.get("магазин", "")
    тип = result.get("тип", "расход")

    if not позиции:
        await update.message.reply_text("🤔 Не смогла разобрать позиции.")
        return

    lines = []
    total = 0
    for p in позиции:
        op = {
            "тип": тип,
            "сумма": float(p.get("сумма", 0)),
            "категория": p.get("категория", "Прочее"),
            "подкатегория": p.get("подкатегория", ""),
            "магазин": магазин,
            "описание": p.get("описание", ""),
            "уверенность": 0.9
        }
        write_operation(op, source=source)
        total += op["сумма"]
        lines.append(f"• {op['описание']} — {op['сумма']:.0f} ₽ ({op['категория']})")

    await update.message.reply_text(
        f"💸 Записано {len(позиции)} позиций!\n\n"
        + "\n".join(lines)
        + f"\n\n💰 Итого: *{total:.0f} ₽*",
        parse_mode="Markdown"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    await update.message.reply_text("⏳ Записываю...")

    try:
        result = classify_text(text)

        # Детальный ввод с несколькими позициями
        if result.get("мультизапись"):
            await _send_multi(update, result, source="telegram_текст")
            return

        if not result.get("сумма"):
            amount = extract_amount(text)
            if amount:
                result["сумма"] = amount

        if result.get("сумма") is not None:
            try:
                result["сумма"] = float(str(result["сумма"]).replace(",", ".").replace(" ", ""))
            except (ValueError, TypeError):
                result["сумма"] = None

        if not result.get("сумма"):
            await update.message.reply_text(
                "🤔 Не смогла найти сумму в сообщении.\n"
                "Попробуй написать так: *кофе 350* или *такси 300*",
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
                f"💰 *{result['сумма']:.0f} ₽*\n"
                f"📂 {cat}{subcat_str}{store_str}\n"
                f"📝 {result.get('описание', text)}"
                f"{warning}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ Не удалось записать в таблицу.\n"
                "Проверь что бот добавлен как редактор в Google Таблицу."
            )

    except Exception as e:
        logger.error(f"Ошибка handle_text: {e}")
        await update.message.reply_text("❌ Что-то пошло не так. Попробуй ещё раз.")
