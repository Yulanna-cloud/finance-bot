"""
Обработчик файлов банковских выписок.
Поддерживает CSV, Excel и PDF от Сбербанка.
"""

import logging
import io
import os
import re
import json
import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes
from services.gemini_service import parse_bank_statement
from services.sheets_service import write_operations_batch

logger = logging.getLogger(__name__)

MAX_OPERATIONS = 200

# Супермаркеты и продуктовые — пропускаем при загрузке выписки
SKIP_MERCHANTS = [
    "pyaterochka", "пятерочка", "magnit", "магнит", "krasnoe", "красное",
    "beloe", "белое", "находка", "ежик", "светофор", "монеточка",
    "perekrestok", "перекресток", "lenta", "лента", "вкусвилл", "spar",
    "дикси", "окей", "fix price", "самокат"
]
# Убрали "magazin" и "magazin 1" — это реальные магазины, их надо записывать


def parse_sber_pdf(pdf_bytes: bytes) -> list:
    """Парсит PDF выписку Сбербанка через Groq."""
    try:
        import pypdf
        from services.gemini_service import groq_client

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"

        logger.info(f"PDF прочитан, длина текста: {len(full_text)}")

        if not groq_client:
            logger.error("GROQ_API_KEY не найден")
            return []

        prompt = f"""Ты разбираешь банковскую выписку Сбербанка. Верни ТОЛЬКО JSON массив без markdown.

СТРОГИЕ ПРАВИЛА ФИЛЬТРАЦИИ (эти операции НЕ включать вообще):
1. Если в описании есть: "SBERBANK ONL@IN", "VKLAD-KARTA", "KARTA-VKLAD" — это переброски между своими счетами, ПРОПУСТИ
2. Если в описании есть: PYATEROCHKA, KRASNOE, BELOE, MAGNIT, LENTA, PEREKRESTOK, DIXI, SPAR — это продуктовые супермаркеты, ПРОПУСТИ
3. Если в описании есть "Нэсп Без Идентификации" или "Без Идентификации" — ПРОПУСТИ

Суммы со знаком + это ДОХОДЫ, без знака или с минусом — РАСХОДЫ.
Снятие наличных (ATM, банкомат, выдача наличных) — тип "наличные".

Для каждой подходящей операции верни объект строго в таком формате:
{{"дата":"DD.MM.YYYY","сумма":число положительное,"тип":"расход или доход или наличные","категория":"одна из списка","магазин":"название магазина или пустая строка","описание":"пустая строка если есть магазин, иначе краткое описание","получатель":"имя если перевод человеку иначе пустая строка"}}

Категории: Кафе, Транспорт, Одежда, Дом, Медицина, Коммуналка, Переводы, Дети, Подписки, Доход, Наличные, Прочее

ПРАВИЛА для поля "магазин":
- Если операция в магазине (не перевод человеку) — пиши название магазина в поле "магазин", поле "описание" оставь пустым
- Примеры: "BUTIK LILIYA STERLITAMAK RUS" → магазин="Butik Liliya", описание=""
- "IP EFIMOVA STERLITAMAK RUS" → магазин="IP Efimova", описание=""
- "MIRA STERLITAMAK RUS" → магазин="Mira", описание=""
- "MAGAZIN 1 STERLITAMAK RUS" → магазин="Magazin 1", описание=""
- "MIRA STERLITAMAK RUS" с категорией Рестораны и кафе → магазин="Mira", категория="Кафе", описание=""
- Для переводов людям: магазин="", описание="", получатель="Имя Фамилия"

ПРАВИЛА категоризации переводов людям:
- "Маргарита" в описании → категория="Дети", получатель="Маргарита П.", тип="расход"
- "Диана Ш" или "Ш. Диана" → если приход: категория="Доход", получатель="Диана Ш.", тип="доход"; если расход: категория="Дети", получатель="Диана Ш.", тип="расход"
- "Алексей П" или "П. Алексей" → если приход: категория="Доход", получатель="Алексей П.", тип="доход"; если расход: категория="Переводы", получатель="Алексей П.", тип="расход"
- "Раиса" или "Г. Райса" → категория="Прочее", получатель="Раиса Г."
- "Яндекс" и операция является ПРИХОДОМ (сумма со знаком +) → категория="Доход", получатель="Алексей П.", тип="доход", магазин="Яндекс", описание=""
- "Яндекс" и операция является РАСХОДОМ → категория="Подписки", магазин="Яндекс", описание=""
- Любой другой перевод конкретному человеку → категория="Переводы", получатель=имя из описания

Текст выписки:
{full_text[:6000]}"""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        logger.info(f"Groq ответ (первые 500 символов): {raw[:500]}")

        items = json.loads(raw)
        if not isinstance(items, list):
            logger.error(f"Groq вернул не массив: {type(items)}")
            return []

        result = []
        for item in items:
            try:
                amount = float(str(item.get("сумма", 0)).replace(",", "."))
            except (ValueError, TypeError):
                continue
            if amount <= 0:
                continue

            # Дополнительная проверка — на случай если Groq всё же включил супермаркет
            описание = item.get("описание", "").lower()
            магазин = item.get("магазин", "").lower()
            проверка = f"{описание} {магазин}"
            if any(s in проверка for s in SKIP_MERCHANTS):
                logger.info(f"Пропускаем супермаркет: {item.get('магазин') or item.get('описание')}")
                continue

            result.append({
                "дата": item.get("дата", ""),
                "сумма": amount,
                "тип": item.get("тип", "расход"),
                "категория": item.get("категория", "Прочее"),
                "подкатегория": "",
                "магазин": item.get("магазин", ""),
                "описание": item.get("описание", ""),
                "получатель": item.get("получатель", ""),
                "уверенность": 0.9
            })

        logger.info(f"Разобрано операций: {len(result)}")
        return result

    except ImportError:
        logger.error("pypdf не установлен")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от Groq: {e}")
        return []
    except Exception as e:
        logger.error(f"Ошибка парсинга PDF: {e}", exc_info=True)
        return []


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or ""
    ext = os.path.splitext(filename)[1].lower()

    if ext not in (".csv", ".xlsx", ".xls", ".pdf"):
        await update.message.reply_text(
            "📄 Поддерживаю файлы *CSV*, *Excel* (.xlsx, .xls) и *PDF* (выписка Сбербанка)",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"📊 Читаю выписку *{filename}*...", parse_mode="Markdown")

    try:
        file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        file_bytes = buf.read()

        operations = []

        if ext == ".pdf":
            await update.message.reply_text("📄 Определила формат Сбербанка. Анализирую операции...")
            operations = parse_sber_pdf(file_bytes)
        else:
            buf = io.BytesIO(file_bytes)
            df = read_statement_file(buf, ext, filename)

            if df is None or df.empty:
                await update.message.reply_text("❌ Не смогла прочитать файл.")
                return

            if len(df) > MAX_OPERATIONS:
                df = df.head(MAX_OPERATIONS)

            text_repr = df.to_string(index=False, max_rows=MAX_OPERATIONS)
            operations = parse_bank_statement(text_repr)

        if not operations:
            await update.message.reply_text(
                "❌ Не нашла операций для записи.\n"
                "Возможно все операции — это супермаркеты (они пропускаются) "
                "или внутренние переброски между счетами."
            )
            return

        total_expense = sum(op.get("сумма", 0) for op in operations if op.get("тип") == "расход")
        total_income = sum(op.get("сумма", 0) for op in operations if op.get("тип") == "доход")
        cash_count = sum(1 for op in operations if op.get("тип") == "наличные")

        preview_lines = ["📋 *Найдены операции:*\n"]
        for op in operations[:10]:
            тип = op.get("тип", "")
            emoji = "💰" if тип == "доход" else "🏧" if тип == "наличные" else "💸"
            получатель = op.get("получатель", "")
            магазин = op.get("магазин", "")
            описание = op.get("описание", "")
            показать = получатель or магазин or описание
            preview_lines.append(
                f"{emoji} {показать[:40]} — "
                f"{op.get('сумма', 0):,.0f} ₽ ({op.get('категория', '')})"
            )
        if len(operations) > 10:
            preview_lines.append(f"_...и ещё {len(operations) - 10} операций_")

        await update.message.reply_text("\n".join(preview_lines), parse_mode="Markdown")

        ok, errors = write_operations_batch(operations, source="выписка_сбер")

        msg = (
            f"✅ Выписка обработана!\n\n"
            f"📥 Записано: *{ok}* операций\n"
            f"💸 Расходы: *{total_expense:,.0f} ₽*\n"
            f"💰 Доходы: *{total_income:,.0f} ₽*"
        )
        if cash_count:
            msg += f"\n🏧 Снятие наличных: {cash_count} раз (не считается)"
        if errors:
            msg += f"\n⚠️ Не записалось: {errors}"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка handle_file: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при обработке файла.")


def read_statement_file(buf: io.BytesIO, ext: str, filename: str) -> pd.DataFrame:
    try:
        if ext in (".xlsx", ".xls"):
            engine = "openpyxl" if ext == ".xlsx" else "xlrd"
            for header_row in [0, 1, 2]:
                try:
                    df = pd.read_excel(buf, engine=engine, header=header_row)
                    buf.seek(0)
                    if len(df) > 0 and len(df.columns) > 2:
                        return df
                except Exception:
                    buf.seek(0)
                    continue
        else:
            for encoding in ["utf-8", "cp1251", "utf-8-sig"]:
                for sep in [";", ",", "\t"]:
                    try:
                        buf.seek(0)
                        df = pd.read_csv(buf, encoding=encoding, sep=sep, on_bad_lines="skip")
                        if len(df) > 0 and len(df.columns) > 2:
                            return df
                    except Exception:
                        continue
    except Exception as e:
        logger.error(f"Ошибка чтения файла: {e}")
    return None
