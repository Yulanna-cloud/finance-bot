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

SKIP_MERCHANTS = [
    "pyaterochka", "пятерочка", "magnit", "магнит", "krasnoe", "красное",
    "beloe", "белое", "magazin", "находка", "ежик", "светофор", "монеточка",
    "perekrestok", "перекресток", "lenta", "лента", "вкусвилл", "spar",
    "дикси", "окей", "fix price", "самокат"
]


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

СТРОГИЕ ПРАВИЛА ФИЛЬТРАЦИИ:
1. НИКОГДА не включай операции где в описании есть: "SBERBANK ONL@IN", "VKLAD-KARTA", "KARTA-VKLAD" — это внутренние переброски между счетами
2. НИКОГДА не включай супермаркеты — если в описании есть любое из этих слов: PYATEROCHKA, KRASNOE, BELOE, MAGAZIN, MAGNIT, LENTA, PEREKRESTOK, DIXI, SPAR — пропусти операцию полностью, её нет в результате
3. Суммы со знаком + это доходы, без знака или с - это расходы
4. Снятие наличных (ATM, банкомат, выдача наличных) — тип "наличные"

Для каждой подходящей операции верни объект:
{{"дата":"DD.MM.YYYY","сумма":число положительное,"тип":"расход или доход или наличные","категория":"одна из списка","описание":"краткое описание операции","получатель":"имя если это перевод конкретному человеку иначе пустая строка"}}

Категории: Кафе, Транспорт, Одежда, Дом, Медицина, Коммуналка, Переводы, Дети, Подписки, Доход, Наличные, Прочее

Правила категоризации переводов людям:
- Если в описании "Маргарита" → категория Дети, получатель "Маргарита П.", тип расход
- Если в описании "Диана Ш" или "Ш. Диана" → если приход то Доход получатель "Диана Ш.", если расход то Дети получатель "Диана Ш."
- Если в описании "Алексей П" или "П. Алексей" → если приход то Доход получатель "Алексей П.", если расход то категория Переводы получатель "Алексей П."
- Если в описании "Раиса" или "Г. Райса" → категория Семья, получатель "Раиса Г."
- Если в описании "Нэсп Без Идентификации" или "Без Идентификации" → ПРОПУСТИ эту операцию
- Любой другой перевод для конкретного человека → категория Переводы, получатель — имя из описания

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
            if any(s in описание for s in SKIP_MERCHANTS):
                logger.info(f"Пропускаем супермаркет: {item.get('описание')}")
                continue

            result.append({
                "дата": item.get("дата", ""),
                "сумма": amount,
                "тип": item.get("тип", "расход"),
                "категория": item.get("категория", "Прочее"),
                "подкатегория": "",
                "магазин": "",
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
            описание = op.get("описание", "")
            показать = f"{описание} → {получатель}" if получатель else описание
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
