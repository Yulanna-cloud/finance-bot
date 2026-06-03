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

UFA_TZ = timezone(timedelta(hours=5))

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

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

FAMILY_SEARCH = {
    "маргарит": "Маргарита П.",
    "диан":     "Диана Ш.",
    "алекс":    "Алексей П.",
    "алёш":     "Алексей П.",
    "райс":     "Райса Г.",
    "салават":  "Салават Г.",
    "дамир":    "Дамир Г.",
    "ольг":     "Ольга Г.",
}

def now_ufa() -> datetime:
    return datetime.now(tz=UFA_TZ)

def month_matches(cell_value: str, target_month: str) -> bool:
    cell = cell_value.strip().lower()
    variants = MONTH_MAP.get(target_month.lower(), [target_month.lower()])
    return any(cell == v for v in variants)

def _get_cell(row: list, i: int) -> str:
    return str(row[i]).strip() if i < len(row) else ""

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

def smart_query(query_text: str) -> dict:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        ops_sheet = spreadsheet.worksheet("ОПЕРАЦИИ")
        ops_rows = ops_sheet.get_values()

        if not ops_rows or len(ops_rows) <= 1:
            return {"ответ": "В таблице пока нет операций."}

        raw_query = query_text.lower()
        for char in [".", ",", "?", "!", "-", "/"]:
            raw_query = raw_query.replace(char, " ")

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

        target_person = None
        target_person_full = None
        for key, full_name in FAMILY_SEARCH.items():
            if key in raw_query:
                target_person = key
                target_person_full = full_name
                break

        income_words = ["пришло", "приход", "перевел", "перевела", "получил", "получила", "от "]
        is_income_search = any(w in raw_query for w in income_words)

        cat_search = None
        cat_map = {
            "обучени": "Обучение", "танц": "Обучение", "продукт": "Продукты",
            "кафе": "Кафе", "транспорт": "Транспорт", "одежд": "Одежда",
            "медицин": "Медицина", "аптек": "Медицина", "животн": "Животные",
            "красот": "Красота", "подписк": "Подписки",
        }
        for key, cat in cat_map.items():
            if key in raw_query:
                cat_search = cat
                break

        ops_headers = [h.strip().lower() for h in ops_rows[0]]

        def find_col(keywords, default):
            for i, h in enumerate(ops_headers):
                if any(k in h for k in keywords):
                    return i
            return default

        idx_date   = find_col(["дата"],             2)
        idx_month  = find_col(["месяц"],            4)
        idx_type   = find_col(["тип"],              6)
        idx_amount = find_col(["сумма"],            7)
        idx_cat    = find_col(["категори"],         9)
        idx_subcat = find_col(["подкатегори"],      11)
        idx_shop   = find_col(["магазин"],          12)
        idx_desc   = find_col(["товар", "описани"], 13)
        idx_recv   = find_col(["получател"],        14)
        idx_sender = find_col(["отправител"],       27)

        found_lines = []
        total_amount = 0.0

        for row in ops_rows[1:]:
            max_idx = max(idx_cat, idx_subcat, idx_desc, idx_amount,
                         idx_recv, idx_shop, idx_sender)
            while len(row) <= max_idx:
                row.append("")

            row_month  = row[idx_month].strip()
            row_type   = row[idx_type].lower()
            row_cat    = row[idx_cat]
            date       = row[idx_date]
            amount_str = row[idx_amount]

            if target_month and not month_matches(row_month, target_month):
                continue

            all_text = " ".join([row[idx_cat], row[idx_subcat], row[idx_desc], row[idx_recv], row[idx_shop], row[idx_sender]]).lower()
            match = False

            if target_person:
                if target_person in all_text:
                    is_income_row = "доход" in row_type
                    if is_income_search and is_income_row:
                        match = True
                    elif not is_income_search:
                        match = True
            elif cat_search:
                if cat_search.lower() in row_cat.lower():
                    match = True

            if not match:
                continue

            try:
                amount_num = float(str(amount_str).replace(" ", "").replace("\xa0", "").replace(",", "."))
                total_amount += amount_num
            except ValueError:
                pass

            label = row[idx_recv] or row[idx_sender] or row[idx_desc] or row[idx_shop] or row[idx_cat]
            found_lines.append(f"📅 {date} | 💰 {amount_str} ₽ | _{label}_")

        if not found_lines:
            return {"ответ": "Ничего не нашлось"}

        lines = [
            f"🔍 Результаты:",
            f"💰 Итого: *{total_amount:,.0f} ₽*",
            "\n📋 Записи:"
        ]
        lines.extend(found_lines[-10:])
        return {"ответ": "\n".join(lines)}

    except Exception as e:
        logger.error(f"Ошибка в smart_query: {e}")
        return {"ошибка": "Произошла ошибка при поиске."}
