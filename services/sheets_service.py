"""
Сервис для работы с Google Sheets.
"""

import os
import json
import logging
import time
import uuid
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


def now_ufa():
    return datetime.now(tz=UFA_TZ)


# =========================
# GOOGLE CLIENT
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
# НОМЕР ОПЕРАЦИИ
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
# ОДНА ОПЕРАЦИЯ
# =========================
def write_operation(operation: dict, source: str = "telegram") -> bool:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        now = now_ufa()

        op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
        op_time = now.strftime("%H:%M")

        op_id = get_next_op_id(sheet)

        # ❗ одиночная операция = отдельная группа
        group_id = f"G-SINGLE-{uuid.uuid4().hex[:8]}"

        confidence = operation.get("уверенность", 0.8)
        status = "обработано" if confidence >= 0.8 else "требует проверки"

        row = [
            op_id,
            group_id,
            op_date,
            op_time,
            MONTH_NAMES_RU[now.month],
            str(now.year),
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
# ПАКЕТНАЯ ЗАПИСЬ (ГЛАВНОЕ ИСПРАВЛЕНИЕ)
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
        rows = []

        # ❗ ВАЖНО: 1 ГРУППА = 1 ДОКУМЕНТ (чек / выписка)
        batch_group_id = f"G-BATCH-{uuid.uuid4().hex[:8]}"

        for operation in operations:
            last_num += 1
            op_id = f"OP-{last_num:04d}"

            op_date = operation.get("дата") or now.strftime("%d.%m.%Y")
            op_time = now.strftime("%H:%M")

            confidence = operation.get("уверенность", 0.9)
            status = "обработано" if confidence >= 0.8 else "требует проверки"

            row = [
                op_id,
                batch_group_id,   # ❗ ВСЕ строки одной выписки = 1 группа
                op_date,
                op_time,
                MONTH_NAMES_RU[now.month],
                str(now.year),
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
            time.sleep(1)
            if write_operation(op, source):
                ok += 1
            else:
                errors += 1

        return ok, errors

def smart_query(query_text: str) -> dict:
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.worksheet("ОПЕРАЦИИ")

        rows = sheet.get_all_values()

        if not rows or len(rows) <= 1:
            return {"ответ": "В таблице пока нет операций."}

        query = query_text.lower()

        found = []
        total = 0.0

        headers = [h.lower() for h in rows[0]]

        def idx(name):
            for i, h in enumerate(headers):
                if name in h:
                    return i
            return 0

        i_date = idx("дата")
        i_sum = idx("сумма")
        i_cat = idx("категория")
        i_shop = idx("магазин")

        for row in rows[1:]:
            while len(row) <= max(i_date, i_sum, i_cat, i_shop):
                row.append("")

            text = " ".join(row).lower()

            if query not in text:
                continue

            try:
                amount = float(str(row[i_sum]).replace(" ", "").replace(",", "."))
                total += amount
            except:
                amount = 0

            found.append(f"{row[i_date]} | {row[i_shop]} | {amount} ₽")

        if not found:
            return {"ответ": "Ничего не найдено"}

        return {
            "ответ": "🔎 Найдено:\n" +
                     "\n".join(found[-10:]) +
                     f"\n\nИтого: {total:.2f} ₽"
        }

    except Exception as e:
        logger.error(f"smart_query error: {e}")
        return {"ошибка": "ошибка поиска"}
