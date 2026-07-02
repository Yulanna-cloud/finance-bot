"""
Рендер месячного отчёта в картинку (PNG) через Pillow.
Заменяет плохо читаемый текстовый список категорий на наглядные полоски.
Никаких тяжёлых зависимостей — только Pillow, который уже есть.
"""
import io
import os
import logging

logger = logging.getLogger(__name__)

# Кандидаты шрифтов: сначала переменная окружения, потом путь Debian
# (fonts-dejavu-core ставится в Dockerfile), потом Windows — для локальных тестов.
_FONT_CANDIDATES = {
    "regular": [
        os.environ.get("REPORT_FONT", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "bold": [
        os.environ.get("REPORT_FONT_BOLD", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
}

# Цвета
BG        = (255, 255, 255)
INK       = (26, 26, 25)
MUTED     = (137, 135, 129)
BAR       = (42, 120, 214)
TILE_BG   = (241, 239, 232)
GREEN     = (25, 158, 112)
RED       = (210, 59, 59)
LINE      = (225, 224, 217)


def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES["bold" if bold else "regular"]:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _fmt(n: float) -> str:
    """22400 -> '22 400 ₽'"""
    try:
        return f"{float(n):,.0f}".replace(",", " ") + " ₽"
    except (ValueError, TypeError):
        return f"{n} ₽"


def _text_w(draw, text, font) -> int:
    return draw.textbbox((0, 0), text, font=font)[2]


def render_report_image(
    month_name: str,
    year: int,
    income: float,
    expenses: float,
    balance: float,
    categories: dict,
    prev_expenses: float | None = None,
    prev_month_name: str | None = None,
) -> io.BytesIO | None:
    """Возвращает BytesIO с PNG или None при ошибке (тогда вызывающий код
    откатится на текстовый отчёт)."""
    try:
        from PIL import Image, ImageDraw

        # Показываем ВСЕ категории с расходом > 0, по убыванию — это отчёт,
        # видно должно быть всё. Картинка просто станет выше, на телефоне листается.
        items = [(k, float(v)) for k, v in categories.items() if float(v) > 0]
        items.sort(key=lambda x: x[1], reverse=True)

        # Уже и с более крупным шрифтом — так текст читается крупнее после того,
        # как Telegram ужмёт картинку под ширину экрана телефона.
        W = 720
        pad = 34
        header_h = 62
        tiles_h = 100
        cmp_h = 36 if prev_expenses is not None else 0
        row_h = 52
        chart_top = pad + header_h + tiles_h + cmp_h + 18
        chart_h = max(row_h * len(items), row_h)
        H = chart_top + chart_h + pad

        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        f_title = _load_font(34, bold=True)
        f_tile_label = _load_font(18)
        f_tile_val = _load_font(25, bold=True)
        f_cmp = _load_font(21)
        f_cat = _load_font(24)
        f_amt = _load_font(23, bold=True)

        # Заголовок
        d.text((pad, pad), f"Отчёт за {month_name} {year}", font=f_title, fill=INK)

        # Три плитки: Доходы / Расходы / Остаток
        tiles_y = pad + header_h
        gap = 16
        tile_w = (W - 2 * pad - 2 * gap) // 3
        bal_color = GREEN if balance >= 0 else RED
        bal_text = ("+" if balance >= 0 else "−") + _fmt(abs(balance))
        tiles = [
            ("Доходы", _fmt(income), GREEN),
            ("Расходы", _fmt(expenses), INK),
            ("Остаток", bal_text, bal_color),
        ]
        for i, (label, val, color) in enumerate(tiles):
            x = pad + i * (tile_w + gap)
            d.rounded_rectangle([x, tiles_y, x + tile_w, tiles_y + tiles_h - 12],
                                radius=12, fill=TILE_BG)
            d.text((x + 16, tiles_y + 14), label, font=f_tile_label, fill=MUTED)
            d.text((x + 16, tiles_y + 42), val, font=f_tile_val, fill=color)

        # Строка сравнения с прошлым месяцем
        if prev_expenses is not None:
            cy = tiles_y + tiles_h + 2
            diff = expenses - prev_expenses
            ref = prev_month_name or "прошлым месяцем"
            if abs(diff) < 1:
                txt, col = f"Столько же, сколько в {ref}", MUTED
            else:
                arrow = "▲" if diff > 0 else "▼"
                word = "больше" if diff > 0 else "меньше"
                col = RED if diff > 0 else GREEN
                # Процент показываем только если он осмысленный: в прошлом месяце
                # были заметные траты и рост/падение не в разы (иначе «+7605%»).
                show_pct = prev_expenses >= 500 and abs(diff) / prev_expenses <= 3
                pct = f" ({'+' if diff > 0 else '−'}{abs(diff) / prev_expenses * 100:.0f}%)" if show_pct else ""
                txt = f"{arrow} на {_fmt(abs(diff))}{pct} {word}, чем в {ref}"
            d.text((pad, cy), txt, font=f_cmp, fill=col)

        # Полоски по категориям
        max_val = max((v for _, v in items), default=1) or 1
        label_w = 210
        amt_w = 120
        bar_x0 = pad + label_w
        bar_x1 = W - pad - amt_w
        bar_span = bar_x1 - bar_x0
        bar_th = 24

        for i, (name, val) in enumerate(items):
            cy = chart_top + i * row_h
            mid = cy + row_h // 2
            # название слева (обрезаем при необходимости)
            label = name
            while _text_w(d, label, f_cat) > label_w - 12 and len(label) > 4:
                label = label[:-2]
            if label != name:
                label = label[:-1] + "…"
            d.text((pad, mid - 11), label, font=f_cat, fill=INK)
            # полоска
            bw = max(int(bar_span * (val / max_val)), 3)
            d.rounded_rectangle([bar_x0, mid - bar_th // 2, bar_x0 + bw, mid + bar_th // 2],
                                radius=4, fill=BAR)
            # сумма справа
            amt = _fmt(val)
            d.text((W - pad - _text_w(d, amt, f_amt), mid - 11), amt, font=f_amt, fill=INK)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        buf.name = "report.png"
        return buf

    except Exception as e:
        logger.error(f"Ошибка рендера отчёта-картинки: {e}", exc_info=True)
        return None
