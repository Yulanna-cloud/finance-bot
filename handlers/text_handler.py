import logging
import re
from telegram import Update
from telegram.ext import ContextTypes
from services.sheets_service import write_operation, write_operations_batch, smart_query, get_monthly_report, now_ufa
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


STRONG_QUERY_WORDS = [
    "сколько", "покажи", "найди", "расшифруй",
    "детали", "подробно", "из чего", "что входит",
]

def is_query(text: str) -> bool:
    t = text.lower()
    # Шаг 1: сильные вопросительные слова — всегда запрос, даже если есть "перевела"
    if any(k in t for k in STRONG_QUERY_WORDS):
        return True
    # Шаг 2: вопросительный знак
    if "?" in t:
        return True
    # Шаг 3: если есть маркер операции — точно НЕ запрос
    if any(m in t for m in OPERATION_MARKERS):
        return False
    # Шаг 4: остальные поисковые слова
    return any(k in t for k in QUERY_KEYWORDS)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.edit_handler import handle_edit_text
    from handlers.budget_handler import (
        handle_budget, handle_budget_set, handle_budget_delete,
        get_budgets, check_budget_alert, CATEGORIES
    )
    text = update.message.text.strip()

    # Если ждём ввод для редактирования — отдаём туда
    if await handle_edit_text(update, context):
        return

    # Если ждём ввод дохода для планирования
    from handlers.plan_handler import handle_plan_text
    if await handle_plan_text(update, context):
        return

    t_lower = text.lower()

    # Команда: бюджет Продукты 15000
    m = re.match(r"^бюджет\s+(.+?)\s+([\d\s.,]+)$", t_lower)
    if m:
        cat_raw = m.group(1).strip().title()
        cat_match = next((c for c in CATEGORIES if c.lower() == cat_raw.lower()), cat_raw)
        try:
            limit = float(m.group(2).replace(" ", "").replace(",", "."))
            await handle_budget_set(update, context, cat_match, limit)
        except ValueError:
            await update.message.reply_text("Не понял сумму. Напиши: бюджет Продукты 15000")
        return

    # Команда: удалить бюджет Продукты
    m2 = re.match(r"^удалить бюджет\s+(.+)$", t_lower)
    if m2:
        cat_raw = m2.group(1).strip().title()
        cat_match = next((c for c in CATEGORIES if c.lower() == cat_raw.lower()), cat_raw)
        await handle_budget_delete(update, context, cat_match)
        return

    # Просмотр бюджетов
    if t_lower in ("бюджет", "бюджеты", "мой бюджет"):
        await handle_budget(update, context)
        return

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
            await update.message.reply_text("Герман в замешательстве 🤔 Напиши каждую покупку отдельным сообщением, так проще.")
            return

        # Записываем все операции батчем
        operations = [op for op in items if op and op.get("сумма")]
        if not operations:
            await update.message.reply_text("Суммы не нашёл 🕵️ Попробуй: кофе 350")
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
        await update.message.reply_text("Сумму не нашёл 🧐 Напиши, например: кофе 350")
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

        # Проверяем бюджет для расходов
        if тип == "расход":
            cat = data.get("категория", "")
            budgets = get_budgets()
            if cat in budgets:
                now = now_ufa()
                report = get_monthly_report(month=now.month, year=now.year)
                spent = report.get("все_категории", {}).get(cat, 0)
                alert = check_budget_alert(cat, spent, budgets[cat])
                if alert:
                    await update.message.reply_text(alert, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Ошибка записи.")
