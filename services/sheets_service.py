"""
Сервис для работы с Google Sheets.
Записывает операции в лист ОПЕРАЦИИ и ИМПОРТ,
читает категории, делает отчёт.
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# ID твоей таблицы (из URL)
SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def get_sheets_client():
    """Подключается к Google Sheets через Service Account"""
    # Ключ можно задать как файл или как JSON-строку в переменной окружения
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")

    if creds_json:
        # Из переменной окружения (удобно для Railway)
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    elif os.path.exists(creds_file):
        # Из файла (удобно при локальном запуске)
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "Не найдены Google credentials! "
            "Задай GOOGLE_CREDENTIALS_JSON или положи credentials.json рядом с ботом."
        )

    return gspread.authorize(creds)


def get_next_op_id(sheet) -> str:
    """Генерирует следующий ID операции вида OP-XXXX"""
    try:
        all_ids = sheet.col_values(1)  # Колонка ID
        op_ids = [x for x in all_ids if x.startswith("OP-")]
        if not op_ids:
            return "OP-0001"
        last_num = max(int(x.replace("OP-", "")) for x in op_ids)
        return f"OP-{last_num + 1:04d}"
    except Exception:
        return f"OP-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def write_operation(operation: dict, source: str = "telegram") -> bool:
    """
    Записывает одну операцию в лист ОПЕРАЦИИ.

    operation — словарь с ключами:
        сумма, тип, категория, подкатегория, магазин,
        описание, уверенность, дата (опционально)
    source — откуда пришло: голос, чек, выписка, текст
    """
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        now = datetime.now()
        op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
        op_time = now.strftime("%H:%M")
        month = now.strftime("%B")  # Май, Июнь...
        year = now.year

        op_id = get_next_op_id(sheet)
        group_id = op_id.replace("OP-", "G-")

        confidence = operation.get("уверенность", 0.8)
        confirmed = "Нет"
        status = "обработано" if confidence >= 0.8 else "требует проверки"

        row = [
            op_id,                                    # ID
            group_id,                                 # Group ID
            op_date,                                  # Дата
            op_time,                                  # Время
            month,                                    # Месяц
            str(year),                                # Год
            operation.get("тип", "расход"),          # Тип операции
            operation.get("сумма", ""),               # Сумма
            "RUB",                                    # Валюта
            operation.get("категория", "Прочее"),    # Категория
            "",                                       # Ручная категория
            operation.get("подкатегория", ""),       # Подкатегория
            operation.get("магазин", ""),            # Магазин
            operation.get("описание", ""),           # Товар/Описание
            "",                                       # Получатель
            "карта",                                  # Способ оплаты
            "",                                       # Счет
            source,                                   # Источник данных
            operation.get("исходный_текст", ""),     # Исходный текст
            "",                                       # Комментарий
            "",                                       # Теги
            "",                                       # Чек ID
            str(confidence),                          # AI уверенность
            confirmed,                                # Подтверждено
            status,                                   # Статус
            "gemini",                                 # AI модель
            now.strftime("%d.%m.%Y %H:%M"),          # Дата создания
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Записана операция {op_id}: {operation.get('категория')} {operation.get('сумма')}₽")
        return True

    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {e}")
        return False


def write_operations_batch(operations: list, source: str) -> tuple[int, int]:
    """
    Записывает несколько операций (для чеков и выписок).
    Возвращает (успешно, с ошибками).
    """
    ok = 0
    errors = 0
    for op in operations:
        if write_operation(op, source):
            ok += 1
        else:
            errors += 1
    return ok, errors


def get_monthly_report(month: Optional[int] = None, year: Optional[int] = None) -> dict:
    """
    Читает лист ОПЕРАЦИИ и считает отчёт за месяц.
    Возвращает dict с итогами по категориям.
    """
    try:
        now = datetime.now()
        target_month = month or now.month
        target_year = year or now.year

        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        all_rows = sheet.get_all_records()

        # Названия месяцев для сравнения
        month_names_ru = {
            1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
            5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
            9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
        }
        target_month_name = month_names_ru[target_month]

        expenses = {}   # категория → сумма
        income = 0.0
        total_expense = 0.0
        count = 0

        for row in all_rows:
            # Фильтр по месяцу — пробуем несколько форматов дат
            row_date = str(row.get("Дата", ""))
            row_month = str(row.get("Месяц", ""))
            row_year = str(row.get("Год", ""))
            row_type = str(row.get("Тип операции", "")).lower()

            # Парсим дату если месяц/год не заполнены
            in_period = False
            if row_month.lower() == target_month_name.lower() and str(target_year) in row_year:
                in_period = True
            elif row_date:
                try:
                    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y"):
                        try:
                            dt = datetime.strptime(row_date[:10], fmt)
                            if dt.month == target_month and dt.year == target_year:
                                in_period = True
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            if not in_period:
                continue

            try:
                amount = float(str(row.get("Сумма", 0)).replace(",", ".").replace(" ", "") or 0)
            except (ValueError, TypeError):
                continue

            if amount <= 0:
                continue

            count += 1
            category = str(row.get("Категория", "Прочее") or "Прочее")

            if row_type == "доход":
                income += amount
            elif row_type in ("расход", ""):
                total_expense += amount
                expenses[category] = expenses.get(category, 0) + amount

        # Топ категорий
        top_categories = sorted(expenses.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "месяц": target_month_name,
            "год": target_year,
            "доходы": income,
            "расходы": total_expense,
            "остаток": income - total_expense,
            "количество": count,
            "топ_категорий": top_categories,
            "все_категории": expenses,
        }

    except Exception as e:
        logger.error(f"Ошибка получения отчёта: {e}")
        return {"ошибка": str(e)}
