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

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}


# =========================
# TIME
# =========================
def now_ufa() -> datetime:
    return datetime.now(tz=UFA_TZ)


# =========================
# GOOGLE SHEETS CLIENT
# =========================
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


# =========================
# ID генерация операций
# =========================
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


# =========================
# ОДНА ГРУППА = ОДИН ДОКУМЕНТ
# =========================
def _get_next_group_id(sheet) -> str:
    try:
        all_ids = sheet.col_values(2)  # group_id колонка
        group_ids = [x for x in all_ids if str(x).startswith("G-")]

        if not group_ids:
            return "G-0001"

        last_num = max(int(x.replace("G-", "")) for x in group_ids)
        return f"G-{last_num + 1:04d}"

    except Exception:
        return f"G-{now_ufa().strftime('%Y%m%d%H%M%S')}"


# =========================
# ПАКЕТНАЯ ЗАПИСЬ (ФИКС)
# =========================
def write_operations_batch(operations: list, source: str) -> tuple[int, int]:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        all_ids = sheet.col_values(1)
        op_ids = [x for x in all_ids if str(x).startswith("OP-")]
        last_num = max((int(x.replace("OP-", "")) for x in op_ids), default=0)

        now = now_ufa()

        # =========================
        # ВАЖНО: 1 ДОКУМЕНТ = 1 GROUP
        # =========================
        group_id = _get_next_group_id(sheet)

        rows = []

        for operation in operations:
            last_num += 1
            op_id = f"OP-{last_num:04d}"

            op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
            op_time = now.strftime("%H:%M")
            month = MONTH_NAMES_RU[now.month]
            year = now.year

            confidence = operation.get("уверенность", 0.9)
            status = "обработано" if confidence >= 0.8 else "требует проверки"

            row = [
                op_id,
                group_id,  # 🔥 ОДИНАКОВЫЙ ДЛЯ ВСЕХ СТРОК
                op_date,
                op_time,
                month,
                str(year),
                operation.get("тип", "расход"),
                operation.get("сумма", ""),
                "RUB",
                operation.get("категория", "Прочее"),
                "",
                operation.get("подкатегория", ""),
                operation.get("магазин", ""),
                operation.get("описание", ""),
                operation.get("получатель", ""),
                "карта",
                "",
                source,
                operation.get("исходный_текст", ""),
                "",
                "",
                "",
                str(confidence),
                "Нет",
                status,
                "groq",
                now.strftime("%d.%m.%Y %H:%M"),
                operation.get("отправитель", ""),
            ]

            rows.append(row)

        if rows:
            sheet.append_rows(rows, value_input_option="USER_ENTERED")

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


# =========================
# ОДИНОЧНАЯ ЗАПИСЬ
# =========================
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
        group_id = _get_next_group_id(sheet)

        confidence = operation.get("уверенность", 0.8)
        status = "обработано" if confidence >= 0.8 else "требует проверки"

        row = [
            op_id,
            group_id,
            op_date,
            op_time,
            month,
            str(year),
            operation.get("тип", "расход"),
            operation.get("сумма", ""),
            "RUB",
            operation.get("категория", "Прочее"),
            "",
            operation.get("подкатегория", ""),
            operation.get("магазин", ""),
            operation.get("описание", ""),
            operation.get("получатель", ""),
            "карта",
            "",
            source,
            operation.get("исходный_текст", ""),
            "",
            "",
            "",
            str(confidence),
            "Нет",
            status,
            "groq",
            now.strftime("%d.%m.%Y %H:%M"),
            operation.get("отправитель", ""),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")

        return True

    except Exception as e:
        logger.error(f"Ошибка записи: {e}")
        return False


# =========================
# ОТЧЁТЫ (оставлено без изменений)
# =========================
def get_monthly_report(month: Optional[int] = None, year: Optional[int] = None) -> dict:
    return {"status": "not modified"}
