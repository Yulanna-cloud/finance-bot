import logging
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import write_operation, write_operations_batch, smart_query
from services.gemini_service import classify_text, classify_text_multi, is_multi_line_input

logger = logging.getLogger(__name__)

# Слова, которые ОДНОЗНАЧНО означают запись операции — проверяем В ПЕРВУЮ ОЧЕРЕДЬ
# Если хоть одно из них есть в тексте — это не поисковый запрос, а операция
OPERATION_MARKERS = [
    "карта", "наличные", "сбп", "нал",
    "купила", "купил", "купили",
    "заплатила", "заплатил",
    "потратила", "потратил",
    "получила", "получил",
    "оплатила", "оплатил",
    "перевела", "перевел",
    "прислала", "прислал",
]

# Слова, которые ОДНОЗНАЧНО означают поисковый запрос
QUERY_KEYWORDS = [
    "сколько",
    "покажи",
    "найди",
    "поиск",
    "отчет",
    "статистика",
    "потрачено на",
    "расходы на",
    "доходы от",
    "история",
    "последние",
    "расшифруй",
    "детали",
    "подробно",
    "из чего",
    "что входит",
]


def is_query(text: str) -> bool:
    t = text.lower()
    # Шаг 1: если есть маркер операции — точно НЕ запрос
    if any(m in t for m in OPERATION_MARKERS):
        return False
    # Шаг 2: явный вопрос
    if "?" in t:
        return True
    # Шаг 3: ключевые слова поиска
    return any(k in t for k in QUERY_KEYWORDS)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Поисковый запрос
    if is_query(text):
        result = smart_query(text)
        answer = result.get("ответ") or result.get("ошибка") or "Ничего не нашлось."
        await update.message.reply_text(answer, parse_mode="Markdown")
        return

    # Несколько покупок в одном сообщении
    if is_multi_line_input(text):
        items = classify_text_multi(text)
        if not items:
            await update.message.reply_text("Не смогла разобрать. Напиши каждую покупку отдельным сообщением.")
            return

        # Записываем все операции батчем
        operations = [op for op in items if op and op.get("сумма")]
        if not operations:
            await update.message.reply_text("Не нашла суммы. Напиши: кофе 350")
            return

        ok, errors = write_operations_batch(operations, source="текст")

        # Формируем ответ
        lines = []
        total = 0
        for op in operations:
            emoji = "💰" if op.get("тип") == "доход" else "💸"
            store = f" ({op['магазин']})" if op.get("магазин") else ""
            lines.append(f"{emoji} {op['сумма']:,.0f} ₽ — {op.get('категория','')}{store}")
            total += op.get("сумма", 0)

        msg = "\n".join(lines)
        msg += f"\n\n💰 Итого: *{total:,.0f} ₽*"
        msg += f"\n📥 Записано: {ok} из {len(operations)}"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # Одна операция
    data = classify_text(text)
    if not data or not data.get("сумма"):
        await update.message.reply_text("Не смогла найти сумму. Напиши: кофе 350")
        return

    # Мультизапись (детальный разбор с магазином)
    if data.get("мультизапись") and data.get("позиции"):
        operations = []
        for item in data["позиции"]:
            try:
                amount = float(str(item.get("сумма", 0)).replace(",", "."))
            except (ValueError, TypeError):
                continue
            if amount <= 0:
                continue
            operations.append({
                "тип": data.get("тип", "расход"),
                "сумма": amount,
                "категория": item.get("категория", "Продукты"),
                "подкатегория": item.get("подкатегория", ""),
                "магазин": data.get("магазин", ""),
                "описание": item.get("описание", ""),
                "получатель": "",
                "отправитель": "",
                "уверенность": 0.9,
            })
        if operations:
            ok, errors = write_operations_batch(operations, source="текст")
            total = sum(op["сумма"] for op in operations)
            lines = [f"• {op['описание'] or op['категория']} — {op['сумма']:,.0f} ₽" for op in operations[:10]]
            store_str = f"🏪 *{data['магазин']}*\n" if data.get("магазин") else ""
            await update.message.reply_text(
                f"🧾 Записано!\n{store_str}" + "\n".join(lines) +
                f"\n💰 Итого: *{total:,.0f} ₽*\n📥 {ok} позиций",
                parse_mode="Markdown"
            )
            return

    # Обычная одиночная запись
    ok = write_operation(data)
    if ok:
        тип = data.get("тип", "расход")
        emoji = "💰" if тип == "доход" else "💸"
        msg = f"{emoji} *{data['сумма']:,.0f} ₽* — {data.get('категория', '')}"
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
