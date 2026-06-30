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

# Имена семьи для поиска
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
        logger.info(f"Записана операция {op_id}: {operation.get('тип')} {operation.get('категория')} {operation.get('сумма')}р")
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
            group_id = op_id.replace("OP-", "G-")

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
        all_values = sheet.get_all_values()

        target_month_name = MONTH_NAMES_RU[target_month]
        expenses = {}
        income = 0.0
        total_expense = 0.0
        count = 0

        if len(all_values) <= 1:
            return {
                "месяц": target_month_name, "год": target_year,
                "доходы": 0, "расходы": 0, "остаток": 0,
                "количество": 0, "топ_категорий": [], "все_категории": {},
            }

        headers = [h.strip().lower() for h in all_values[0]]

        def find_col_index(keywords: list, default: int) -> int:
            for kw in keywords:
                for i, h in enumerate(headers):
                    if h == kw.lower():
                        return i
            for kw in keywords:
                for i, h in enumerate(headers):
                    if kw.lower() in h:
                        return i
            return default

        ic_date  = find_col_index(["дата"],                2)
        ic_month = find_col_index(["месяц"],               4)
        ic_year  = find_col_index(["год"],                 5)
        ic_type  = find_col_index(["тип операции", "тип"], 6)
        ic_sum   = find_col_index(["сумма"],               7)
        ic_cat   = find_col_index(["категория"],           9)

        for raw_row in all_values[1:]:
            row_date  = _get_cell(raw_row, ic_date)
            row_month = _get_cell(raw_row, ic_month)
            row_year  = _get_cell(raw_row, ic_year)
            row_type  = _get_cell(raw_row, ic_type).lower()
            row_sum   = _get_cell(raw_row, ic_sum)
            row_cat   = _get_cell(raw_row, ic_cat)

            in_period = False
            if month_matches(row_month, target_month_name) and str(target_year) in row_year:
                in_period = True
            elif row_date:
                for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        dt = datetime.strptime(row_date[:10], fmt)
                        if dt.month == target_month and dt.year == target_year:
                            in_period = True
                        break
                    except ValueError:
                        continue

            if not in_period:
                continue

            try:
                amount = float(
                    row_sum.replace(" ", "").replace("\xa0", "").replace(",", ".") or 0
                )
            except (ValueError, TypeError):
                continue

            if amount <= 0:
                continue
            if row_type in ("наличные", "между счетами"):
                continue

            count += 1
            category = row_cat or "Прочее"

            if row_type == "доход":
                income += amount
            else:
                total_expense += amount
                expenses[category] = expenses.get(category, 0) + amount
                # Для переводов собираем получателей
                if category == "Переводы":
                    ic_recv = find_col_index(["получател"], 14)
                    ic_desc = find_col_index(["описани", "товар"], 13)
                    recv_raw = (_get_cell(raw_row, ic_recv) or _get_cell(raw_row, ic_desc) or "?").lower()
                    # Нормализуем имя через FAMILY_SEARCH
                    recv_norm = None
                    for key, full_name in FAMILY_SEARCH.items():
                        if key in recv_raw:
                            recv_norm = full_name
                            break
                    recv = recv_norm or recv_raw.title() or "?"
                    transfers_detail = expenses.setdefault("__transfers__", {})
                    transfers_detail[recv] = transfers_detail.get(recv, 0) + amount

        top_categories = sorted(
            [(k, v) for k, v in expenses.items() if not k.startswith("__")],
            key=lambda x: x[1], reverse=True
        )[:5]
        logger.info(f"Отчёт за {target_month_name} {target_year}: доходы={income}, расходы={total_expense}, операций={count}")

        transfers_detail = expenses.pop("__transfers__", {})
        clean_expenses = {k: v for k, v in expenses.items() if not k.startswith("__")}

        return {
            "месяц": target_month_name,
            "год": target_year,
            "доходы": income,
            "расходы": total_expense,
            "остаток": income - total_expense,
            "количество": count,
            "топ_категорий": top_categories,
            "все_категории": clean_expenses,
            "переводы_детали": transfers_detail,
        }

    except Exception as e:
        logger.error(f"Ошибка получения отчёта: {e}")
        return {"ошибка": str(e)}


def archive_month(month: Optional[int] = None, year: Optional[int] = None) -> dict:
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

        all_values = ops_sheet.get_all_values()
        очищено = 0
        rows_to_keep = []

        if len(all_values) > 1:
            hdrs = all_values[0]
            h_lower = [h.strip().lower() for h in hdrs]

            def ac(names, default):
                for name in names:
                    for i, h in enumerate(h_lower):
                        if name in h:
                            return i
                return default

            ai_month = ac(["месяц"], 4)
            ai_year  = ac(["год"], 5)
            ai_date  = ac(["дата"], 2)

            for raw_row in all_values[1:]:
                row_month = _get_cell(raw_row, ai_month)
                row_year  = _get_cell(raw_row, ai_year)
                row_date  = _get_cell(raw_row, ai_date)
                in_period = False

                if month_matches(row_month, month_name) and str(year) in row_year:
                    in_period = True
                elif row_date:
                    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                        try:
                            dt = datetime.strptime(row_date[:10], fmt)
                            if dt.month == month and dt.year == year:
                                in_period = True
                            break
                        except ValueError:
                            continue

                if in_period:
                    очищено += 1
                else:
                    rows_to_keep.append(raw_row)

        if очищено > 0:
            ops_sheet.resize(1)
            if rows_to_keep:
                ops_sheet.append_rows(rows_to_keep, value_input_option="USER_ENTERED")

        return {
            "месяц": month_name, "год": year,
            "записей": len(archive_rows),
            "расходы": report["расходы"],
            "доходы": report["доходы"],
            "очищено": очищено
        }

    except Exception as e:
        logger.error(f"Ошибка архивирования: {e}")
        return {"ошибка": str(e)}


def fix_categories_in_sheet() -> dict:
    """Исправляет категории существующих записей в таблице."""
    try:
        client = get_sheets_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("ОПЕРАЦИИ")
        all_values = sheet.get_all_values()
        if len(all_values) <= 1:
            return {"исправлено": 0}

        headers = [h.strip().lower() for h in all_values[0]]
        def find_col(keywords, default):
            for kw in keywords:
                for i, h in enumerate(headers):
                    if kw in h:
                        return i
            return default

        ic_cat  = find_col(["категори"], 9)
        ic_desc = find_col(["описани", "товар"], 13)
        ic_shop = find_col(["магазин"], 12)

        ic_recv = find_col(["получател"], 14)
        ic_type = find_col(["тип"], 6)

        rules = [
            # (ключевые слова в описании/магазине, новая категория)
            (["клод", "claude", "anthropic", "openai", "chatgpt", "чатгпт", "нейросет"], "Подписки ИИ"),
            (["мтс", "tele2", "теле2", "билайн", "мегафон"], "Связь"),
            # Аптека — лекарства, препараты
            (["флуконазол", "флукон", "таблетк", "капс.", "капсул", "мг №",
              "антибиотик", "анальгин", "аспирин", "ибупрофен", "парацетамол",
              "витамин", "мазь", "бинт", "пластырь", "шприц", "микстур",
              "антибактер", "мирмиспрей", "дезинфек", "антисептик"], "Аптека"),
            # Животные — корм
            (["корм", "whiskas", "royal canin", "purina", "felix", "педигри",
              "pedigree", "д/с.", "д/к.", "для собак", "для кошек", "вет."], "Животные"),
        ]

        fixed = 0
        for row_idx, row in enumerate(all_values[1:], start=2):
            desc  = (row[ic_desc]  if ic_desc  < len(row) else "").lower()
            shop  = (row[ic_shop]  if ic_shop  < len(row) else "").lower()
            recv  = (row[ic_recv]  if ic_recv  < len(row) else "").lower()
            rtype = (row[ic_type]  if ic_type  < len(row) else "").lower()
            cat   = row[ic_cat]    if ic_cat   < len(row) else ""
            combined = desc + " " + shop

            # Перевод конкретному человеку, попавший в Продукты → Переводы
            if cat == "Продукты" and recv and rtype == "расход" and not any(
                food in desc for food in ["продукт", "магазин", "пятер", "находк", "лента", "сырок", "хлеб", "молок"]
            ):
                sheet.update_cell(row_idx, ic_cat + 1, "Переводы")
                fixed += 1
                continue

            for keywords, new_cat in rules:
                if any(kw in combined for kw in keywords) and cat != new_cat:
                    sheet.update_cell(row_idx, ic_cat + 1, new_cat)
                    fixed += 1
                    break

        return {"исправлено": fixed}
    except Exception as e:
        logger.error(f"Ошибка fix_categories: {e}")
        return {"ошибка": str(e)}


def smart_query(query_text: str) -> dict:
    """
    Отвечает на вопросы типа:
    - сколько потрачено на Маргариту
    - сколько пришло от Алексея
    - расходы на обучение в июне
    """
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

        # Определяем месяц из запроса
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

        # Определяем кого ищем из семьи
        target_person = None
        target_person_full = None
        for key, full_name in FAMILY_SEARCH.items():
            if key in raw_query:
                target_person = key
                target_person_full = full_name
                break

        # Определяем тип поиска
        income_words = ["пришло", "приход", "перевел", "перевела", "получил", "получила", "от "]
        is_income_search = any(w in raw_query for w in income_words)

        # Определяем режим расшифровки
        is_breakdown = any(w in raw_query for w in ["расшифруй", "детали", "подробно", "из чего", "что входит"])

        # Определяем категорию из запроса
        cat_search = None
        cat_map = {
            "обучени": "Обучение", "танц": "Обучение", "продукт": "Продукты",
            "кафе": "Кафе", "транспорт": "Транспорт", "одежд": "Одежда",
            "медицин": "Медицина", "аптек": "Аптека", "животн": "Животные",
            "красот": "Красота", "подписк": "Подписки", "подписки ии": "Подписки ИИ",
            "клод": "Подписки ИИ", "claude": "Подписки ИИ",
            "связь": "Связь", "мтс": "Связь", "телефон": "Связь",
            "коммунал": "Коммуналка", "интернет": "Интернет",
            "перевод": "Переводы", "переводы": "Переводы",
            "прочее": "Прочее", "табак": "Табак", "алкогол": "Алкоголь",
        }
        for key, cat in cat_map.items():
            if key in raw_query:
                cat_search = cat
                break

        # Находим индексы колонок
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
            # Дополняем строку до нужной длины
            max_idx = max(idx_cat, idx_subcat, idx_desc, idx_amount,
                         idx_recv, idx_shop, idx_sender)
            while len(row) <= max_idx:
                row.append("")

            row_month  = row[idx_month].strip()
            row_type   = row[idx_type].lower()
            row_cat    = row[idx_cat]
            row_subcat = row[idx_subcat]
            row_desc   = row[idx_desc]
            row_recv   = row[idx_recv]
            row_shop   = row[idx_shop]
            row_sender = row[idx_sender]
            date       = row[idx_date]
            amount_str = row[idx_amount]

            # Фильтр по месяцу
            if target_month and not month_matches(row_month, target_month):
                continue

            # Всё что есть в строке для поиска
            all_text = " ".join([
                row_cat, row_subcat, row_desc, row_recv, row_shop, row_sender
            ]).lower()

            match = False

            # Поиск по имени человека
            if target_person:
                if target_person in all_text:
                    is_income_row = "доход" in row_type
                    if is_income_search and is_income_row:
                        match = True
                    elif not is_income_search:
                        match = True

            # Поиск по категории (без имени человека)
            elif cat_search:
                if cat_search.lower() in row_cat.lower():
                    match = True

            if not match:
                continue

            try:
                amount_num = float(
                    str(amount_str).replace(" ", "").replace("\xa0", "").replace(",", ".")
                )
                total_amount += amount_num
            except ValueError:
                pass

            label = row_recv or row_sender or row_desc or row_shop or row_cat
            found_lines.append(f"📅 {date} | 💰 {amount_str} ₽ | _{label}_")

        if not found_lines:
            month_str = f" за {target_month}" if target_month else ""
            person_str = f" по {target_person_full}" if target_person_full else ""
            cat_str = f" категория {cat_search}" if cat_search else ""
            return {"ответ": f"Ничего не нашлось{person_str}{cat_str}{month_str}"}

        month_label = f" за {target_month.title()}" if target_month else ""
        person_label = f" {target_person_full}" if target_person_full else ""
        cat_label = f" {cat_search}" if cat_search else ""

        limit = 30 if is_breakdown else 10
        lines = [
            f"🔍 {'Расшифровка' if is_breakdown else 'Результаты'}{person_label}{cat_label}{month_label}:",
            f"💰 Итого: *{total_amount:,.0f} ₽*",
            f"\n📋 Записи{' (все)' if is_breakdown else ' (последние 10)'}:"
        ]
        lines.extend(found_lines[-limit:])

        return {"ответ": "\n".join(lines)}

    except Exception as e:
        logger.error(f"Ошибка в smart_query: {e}")
        return {"ошибка": "Произошла ошибка при поиске."}
