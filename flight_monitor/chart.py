"""Построение графика изменения цены по истории из SQLite.

Рендерит PNG в память (matplotlib, backend Agg — без GUI) и возвращает байты,
пригодные для отправки в Telegram как фото.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime

import matplotlib

# Backend без GUI — обязателен для работы в фоновом потоке / на сервере.
matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

logger = logging.getLogger(__name__)

# Цвета линий по маршрутам (по порядку)
_COLORS = ["#2E86DE", "#E74C3C", "#27AE60", "#8E44AD"]


def _parse_ts(ts: str) -> datetime | None:
    """Разобрать метку времени из БД ('YYYY-MM-DD HH:MM:SS')."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None


def render_price_chart(
    series_by_route: list[tuple[dict, list[dict]]],
) -> bytes | None:
    """Построить график цен по маршрутам.

    series_by_route — список (route, history), где history отсортирован по
    возрастанию времени: [{ts, price, ...}, ...].
    Возвращает PNG в виде байтов или None, если данных нет вовсе.
    """
    # Подготавливаем точки, отбрасывая записи без валидного времени
    plotted: list[tuple[str, list[datetime], list[int]]] = []
    for route, history in series_by_route:
        xs: list[datetime] = []
        ys: list[int] = []
        for row in history:
            ts = _parse_ts(row["ts"])
            if ts is None:
                continue
            xs.append(ts)
            ys.append(row["price"])
        if xs:
            label = f"{route['origin']} → {route['destination']}"
            plotted.append((label, xs, ys))

    if not plotted:
        return None

    fig, ax = plt.subplots(figsize=(9, 5), dpi=120)

    for idx, (label, xs, ys) in enumerate(plotted):
        color = _COLORS[idx % len(_COLORS)]
        ax.plot(xs, ys, marker="o", markersize=4, linewidth=2, color=color, label=label)
        # Подписываем последнюю (актуальную) цену на графике
        ax.annotate(
            f"{ys[-1]:,}".replace(",", " ") + " ₽",
            xy=(xs[-1], ys[-1]),
            xytext=(6, 6),
            textcoords="offset points",
            fontsize=9,
            color=color,
            fontweight="bold",
        )

    ax.set_title("История цен на авиабилеты", fontsize=13, fontweight="bold")
    ax.set_ylabel("Цена, ₽")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="best", fontsize=9)

    # Ось X — даты. Автоформат подписей, чтобы не наезжали друг на друга.
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    fig.autofmt_xdate(rotation=30, ha="right")

    # Цена по вертикали с разделителями тысяч (пробел)
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _pos: f"{int(v):,}".replace(",", " "))
    )

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
