"""
Сервис для работы с Google Sheets.
"""

import os
import json
import logging
import time
from datetime import datetime
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    elif os.path.exists(creds_file):
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    else:
        raise FileNotFoundError("Не найдены Google credentials!")
    return gspread.authorize(creds)


def get_next_op_id(sheet) -> str:
    try:
        all_ids = sheet.col_values(1)
        op_ids = [x for x in all_ids if str(x).startswith("OP-")]
        if not op_ids:
            return "OP-0001"
        last_num = max(int(x.replace("OP-", "")) for x in op_ids)
        return f"OP-{last_num + 1:04d}"
    except Exception:
        return f"OP-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def write_operation(operation: dict, source: str = "telegram") -> bool:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        now = datetime.now()
        op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
        op_time = now.strftime("%H:%M")
        month = now.strftime("%B")
        year = now.year

        op_id = get_next_op_id(sheet)
        group_id = op_id.replace("OP-", "G-")

        confidence = operation.get("уверенность", 0.8)
        status = "обработано" if confidence >= 0.8 else "требует проверки"

        row = [
            op_id, group_id, op_date, op_time, month, str(year),
            operation.get("тип", "расход"),
            operation.get("сумма", ""),
            "RUB",
            operation.get("категория", "Прочее"),
            "",
            operation.get("подкатегория", ""),
            operation.get("магазин", ""),
            operation.get("описание", ""),
            operation.get("получатель", ""),
            "карта", "",
            source,
            operation.get("исходный_текст", ""),
            "", "", "",
            str(confidence),
            "Нет", status, "groq",
            now.strftime("%d.%m.%Y %H:%M"),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Записана операция {op_id}: {operation.get('категория')} {operation.get('сумма')}₽")
        return True

    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {e}")
        return False


def write_operations_batch(operations: list, source: str) -> tuple[int, int]:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        all_ids = sheet.col_values(1)
        op_ids = [x for x in all_ids if str(x).startswith("OP-")]
        last_num = max((int(x.replace("OP-", "")) for x in op_ids), default=0)

        now = datetime.now()
        rows = []

        for operation in operations:
            last_num += 1
            op_id = f"OP-{last_num:04d}"
            group_id = f"G-{last_num:04d}"

            op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
            op_time = now.strftime("%H:%M")
            month = now.strftime("%B")
            year = now.year

            confidence = operation.get("уверенность", 0.8)
            status = "обработано" if confidence >= 0.8 else "требует проверки"

            row = [
                op_id, group_id, op_date, op_time, month, str(year),
                operation.get("тип", "расход"),
                operation.get("сумма", ""),
                "RUB",
                operation.get("категория", "Прочее"),
                "",
                operation.get("подкатегория", ""),
                operation.get("магазин", ""),
                operation.get("описание", ""),
                operation.get("получатель", ""),
                "карта", "",
                source,
                operation.get("исходный_текст", ""),
                "", "", "",
                str(confidence),
                "Нет", status, "groq",
                now.strftime("%d.%m.%Y %H:%M"),
            ]
            rows.append(row)

        if rows:
            sheet.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info(f"Записано {len(rows)} операций пакетом")

        return len(rows), 0

    except Exception as e:
        logger.error(f"Ошибка пакетной записи: {e}")
        ok = 0
        errors = 0
        for op in operations:
            time.sleep(2)
            if write_operation(op, source):
                ok += 1
            else:
                errors += 1
        return ok, errors


def get_monthly_report(month: Optional[int] = None, year: Optional[int] = None) -> dict:
    try:
        now = datetime.now()
        target_month = month or now.month
        target_year = year or now.year

        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        all_rows = sheet.get_all_records()

        month_names_ru = {
            1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
            5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
            9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
        }
        target_month_name = month_names_ru[target_month]

        expenses = {}
        income = 0.0
        total_expense = 0.0
        count = 0

        for row in all_rows:
            row_date = str(row.get("Дата", ""))
            row_month = str(row.get("Месяц", ""))
            row_year = str(row.get("Год", ""))
            row_type = str(row.get("Тип операции", "")).lower()

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

            if row_type == "наличные":
                continue

            count += 1
            category = str(row.get("Категория", "Прочее") or "Прочее")

            if row_type == "доход":
                income += amount
            elif row_type in ("расход", ""):
                total_expense += amount
                expenses[category] = expenses.get(category, 0) + amount

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


def archive_month(month: Optional[int] = None, year: Optional[int] = None) -> dict:
    """Архивирует операции за месяц и очищает лист ОПЕРАЦИИ."""
    try:
        now = datetime.now()
        if not month:
            if now.month == 1:
                month = 12
                year = now.year - 1
            else:
                month = now.month - 1
                year = now.year

        report = get_monthly_report(month, year)
        if "ошибка" in report:
            return report

        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        ops_sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        archive_sheet = spreadsheet.worksheet("АРХИВ")

        month_names_ru = {
            1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
            5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
            9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
        }
        month_name = month_names_ru[month]
        период = f"{month_name} {year}"
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M")

        archive_rows = []
        for cat, amount in report["все_категории"].items():
            archive_rows.append([
                период, str(year), month_name,
                cat, "расход", round(amount, 2), "", now_str
            ])
        if report["доходы"] > 0:
            archive_rows.append([
                период, str(year), month_name,
                "Доход", "доход", round(report["доходы"], 2), "", now_str
            ])

        if archive_rows:
            archive_sheet.append_rows(archive_rows, value_input_option="USER_ENTERED")

        all_rows = ops_sheet.get_all_records()
        очищено = 0
        rows_to_keep = []

        for row in all_rows:
            row_month = str(row.get("Месяц", ""))
            row_year = str(row.get("Год", ""))
            row_date = str(row.get("Дата", ""))
            in_period = False

            if row_month.lower() == month_name.lower() and str(year) in row_year:
                in_period = True
            elif row_date:
                try:
                    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                        try:
                            dt = datetime.strptime(row_date[:10], fmt)
                            if dt.month == month and dt.year == year:
                                in_period = True
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            if in_period:
                очищено += 1
            else:
                rows_to_keep.append(row)

        if очищено > 0:
            headers = ops_sheet.row_values(1)
            ops_sheet.resize(1)
            if rows_to_keep:
                remaining = [[str(row.get(h, "")) for h in headers] for row in rows_to_keep]
                ops_sheet.append_rows(remaining, value_input_option="USER_ENTERED")

        return {
            "месяц": month_name,
            "год": year,
            "записей": len(archive_rows),
            "расходы": report["расходы"],
            "доходы": report["доходы"],
            "очищено": очищено
        }

    except Exception as e:
        logger.error(f"Ошибка архивирования: {e}")
        return {"ошибка": str(e)}

def smart_query(query_text: str) -> dict:
    """
    Умный поиск по операциям и архиву с правильными заголовками вашей таблицы.
    """
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        
        ops_sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        archive_sheet = spreadsheet.worksheet("Архив") # Имя листа с большой буквы
        
        # 1. Заголовки для листа ОПЕРАЦИИ (23 колонки)
        expected_ops = ["ID", "Group ID", "Дата", "Время", "Month", "Year", "Тип", "Сумма", "Валюта", "Категория", "Подкатегория", "Магазин", "Товар / Описание", "Получатель", "Способ оплаты", "Статус оплаты", "Источник", "Исходный текст", "Confidence", "Confirmed", "Статус", "Проверил", "Дата проверки"]
        
        # 2. Реальные заголовки вашего листа Архив (8 колонок)
        expected_archive = ["Период", "Год", "Месяц", "Категория", "Тип", "Сумма", "Количество операций", "Дата архивирования"]
        
        ops_rows = ops_sheet.get_all_records(expected_headers=expected_ops)
        try:
            archive_rows = archive_sheet.get_all_records(expected_headers=expected_archive)
        except Exception as e:
            logger.error(f"Не удалось прочитать лист Архив: {e}")
            archive_rows = []
            
        query_lower = query_text.lower()
        found_lines = []
        
        # Поиск в активных ОПЕРАЦИЯХ
        for row in ops_rows:
            category = str(row.get("Категория", "")).lower()
            desc = str(row.get("Товар / Описание", "")).lower()
            
            if query_lower in category or query_lower in desc:
                date = row.get("Дата", "—")
                op_type = "💸" if row.get("Тип") == "расход" else "💰"
                amount = row.get("Сумма", "0")
                cat = row.get("Категория", "Прочее")
                d_text = row.get("Товар / Описание", "")
                found_lines.append(f"📅 {date} | {op_type} {amount} ₽ | {cat} | _{d_text}_")
                
        # Поиск в АРХИВЕ (тут структура проще, ищем по Категории)
        for row in archive_rows:
            category = str(row.get("Категория", "")).lower()
            if query_lower in category:
                period = row.get("Период", "—")
                op_type = "💸" if row.get("Тип") == "расход" else "💰"
                amount = row.get("Сумма", "0")
                cat = row.get("Категория", "Прочее")
                found_lines.append(f"🗄️ Архив ({period}) | {op_type} {amount} ₽ | {cat}")

        if not found_lines:
            return {"ответ": f"Ничего не нашлось по запросу «{query_text}»"}
            
        # Формируем красивый текстовый ответ (последние 5 записей)
        lines = [f"🔍 Результаты по запросу «{query_text}» (последние 5):"]
        lines.extend(found_lines[-5:])
            
        return {"ответ": "\n".join(lines)}
    except Exception as e:
        logger.error(f"Ошибка в smart_query: {e}")
        return {"ошибка": str(e)}



