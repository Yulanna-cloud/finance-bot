"""
Сервис для работы с Google Sheets.
"""

import os
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1vd5uDsilhAx8hrpLf88rBuogJIWIMB2LNs9DoyMMTLQ"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Часовой пояс Уфа = UTC+5
UFA_TZ = timezone(timedelta(hours=5))

# Соответствие русских и английских названий месяцев
MONTH_MAP = {
    "январь": ["январь", "january", "jan"],
    "февраль": ["февраль", "february", "feb"],
    "март": ["март", "march", "mar"],
    "апрель": ["апрель", "april", "apr"],
    "май": ["май", "may"],
    "июнь": ["июнь", "june", "jun"],
    "июль": ["июль", "july", "jul"],
    "август": ["август", "august", "aug"],
    "сентябрь": ["сентябрь", "september", "sep"],
    "октябрь": ["октябрь", "october", "oct"],
    "ноябрь": ["ноябрь", "november", "nov"],
    "декабрь": ["декабрь", "december", "dec"],
}

# Русские названия месяцев для записи в таблицу
MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}


def now_ufa() -> datetime:
    """Возвращает текущее время по Уфе (UTC+5)."""
    return datetime.now(tz=UFA_TZ)


def month_matches(cell_value: str, target_month: str) -> bool:
    """Проверяет, совпадает ли значение ячейки с нужным месяцем (рус/англ)."""
    cell = cell_value.strip().lower()
    variants = MONTH_MAP.get(target_month.lower(), [target_month.lower()])
    return any(cell == v for v in variants)


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
        return f"OP-{now_ufa().strftime('%Y%m%d%H%M%S')}"


def write_operation(operation: dict, source: str = "telegram") -> bool:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        now = now_ufa()
        op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
        op_time = now.strftime("%H:%M")
        month = MONTH_NAMES_RU[now.month]
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
            operation.get("отправитель", ""),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Записана операция {op_id}: {operation.get('категория')} {operation.get('сумма')}р")
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

        now = now_ufa()
        rows = []

        for operation in operations:
            last_num += 1
            op_id = f"OP-{last_num:04d}"
            group_id = f"G-{last_num:04d}"

            op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
            op_time = now.strftime("%H:%M")
            month = MONTH_NAMES_RU[now.month]
            year = now.year

            confidence = operation.get("уверенность", 0.9)
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
                operation.get("отправитель", ""),
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
        now = now_ufa()
        target_month = month or now.month
        target_year = year or now.year

        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        all_rows = sheet.get_all_records()

        target_month_name = MONTH_NAMES_RU[target_month]

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
            if month_matches(row_month, target_month_name) and str(target_year) in row_year:
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
        now = now_ufa()
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

        month_name = MONTH_NAMES_RU[month]
        период = f"{month_name} {year}"
        now_str = now_ufa().strftime("%d.%m.%Y %H:%M")

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

            if month_matches(row_month, month_name) and str(year) in row_year:
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
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        ops_sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        try:
            ops_rows = ops_sheet.get_values()
        except Exception as e:
            logger.error(f"Ошибка чтения ОПЕРАЦИЙ: {e}")
            return {"ошибка": "Google Таблицы временно недоступны. Попробуйте еще раз."}

        if not ops_rows or len(ops_rows) <= 1:
            return {"ответ": "В таблице пока нет операций."}

        raw_query = query_text.lower()

        # 1. Распознавание месяцев из запроса
        months_query = {
            "январ": "январь", "феврал": "февраль", "март": "март",
            "апрел": "апрель", "мае": "май", "май": "май", "июн": "июнь",
            "июл": "июль", "август": "август", "сентябр": "сентябрь",
            "октябр": "октябрь", "ноябр": "ноябрь", "декабр": "декабрь"
        }

        target_month = None
        for key, val in months_query.items():
            if key in raw_query:
                target_month = val
                break

        # 2. Очистка от знаков препинания
        for char in [".", ",", "?", "!", "-", "/"]:
            raw_query = raw_query.replace(char, " ")

        # 3. Проверяем, ищем ли Алексея
        is_aleksey_search = "алекс" in raw_query

        # 4. Определяем индексы колонок по частичному совпадению
        ops_headers = [h.strip().lower() for h in ops_rows[0]]

        def find_col(keywords, default):
            for i, h in enumerate(ops_headers):
                if any(k in h for k in keywords):
                    return i
            return default

        idx_date   = find_col(["дата"],             2)
        idx_month  = find_col(["месяц", "month"],   4)
        idx_type   = find_col(["тип"],              6)
        idx_amount = find_col(["сумма"],            7)
        idx_cat    = find_col(["категори"],         9)
        idx_shop   = find_col(["магазин"],          12)
        idx_desc   = find_col(["товар", "описани"], 13)
        idx_recv   = find_col(["получател"],        14)

        found_lines = []
        total_amount = 0.0

        for row in ops_rows[1:]:
            while len(row) <= max(idx_cat, idx_desc, idx_amount, idx_recv, idx_shop):
                row.append("")

            cat_val   = row[idx_cat].lower()
            desc_val  = row[idx_desc].lower()
            recv_val  = row[idx_recv].lower()
            shop_val  = row[idx_shop].lower()
            t_val     = row[idx_type].lower()
            row_month = row[idx_month].strip()

            is_income_row = "доход" in t_val or "доход" in cat_val

            if target_month and not month_matches(row_month, target_month):
                continue

            text_to_search = f"{cat_val} {desc_val} {recv_val} {shop_val}".replace(".", " ")

            match_found = False
            if is_aleksey_search:
                if "алекс" in text_to_search and is_income_row:
                    match_found = True

            if match_found:
                date       = row[idx_date]
                amount_str = row[idx_amount]
                try:
                    amount_num = float(str(amount_str).replace(" ", "").replace(",", "."))
                    total_amount += amount_num
                except ValueError:
                    pass
                d_text = row[idx_recv] or row[idx_desc] or row[idx_shop]
                found_lines.append(f"📅 {date} | 💰 {amount_str} ₽ | _{d_text}_")

        if not found_lines:
            month_print = f" за {target_month}" if target_month else ""
            return {"ответ": f"Ничего не нашлось по запросу «{query_text}»{month_print}"}

        month_str = f" за {target_month.title()}" if target_month else ""
        lines = [
            f"🔍 Результаты{month_str}:",
            f"📊 Всего пришло от Алексея: *{total_amount:,.2f} ₽*",
            "\n📋 Найденные записи (последние 5):"
        ]
        lines.extend(found_lines[-5:])

        return {"ответ": "\n".join(lines)}

    except Exception as e:
        logger.error(f"Ошибка в smart_query: {e}")
        return {"ошибка": "Произошла ошибка при поиске. Попробуйте еще раз."}
