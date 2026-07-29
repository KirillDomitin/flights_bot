"""Отправка уведомлений в Telegram."""
from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# Коды авиакомпаний IATA → человекочитаемые названия
_AIRLINES = {
    "SU": "Аэрофлот",
    "CA": "Air China",
    "MU": "China Eastern",
    "CZ": "China Southern",
    "HU": "Hainan Airlines",
    "3U": "Sichuan Airlines",
    "S7": "S7 Airlines",
    "U6": "Уральские авиалинии",
}

_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def airline_name(code: str | None) -> str:
    """Вернуть название авиакомпании по IATA-коду."""
    if not code:
        return "—"
    return _AIRLINES.get(code, code)


def format_date_ru(depart_date: str) -> str:
    """'2025-09-22' -> '22 сентября 2025'."""
    from datetime import datetime

    try:
        dt = datetime.strptime(depart_date, "%Y-%m-%d")
    except ValueError:
        return depart_date
    return f"{dt.day} {_MONTHS_RU[dt.month]} {dt.year}"


def route_label(origin: str, destination: str) -> str:
    """'MOW', 'PEK' -> 'MOW → PEK'."""
    return f"{origin} → {destination}"


def _format_price(price: int, currency: str | None) -> str:
    """42100, 'rub' -> '42 100 ₽' (для не-rub — код валюты вместо символа)."""
    symbol = "₽" if (currency or "rub").lower() == "rub" else (currency or "")
    return f"{price:,} {symbol}".replace(",", " ").strip()


def _format_change(price: int, previous: dict | None, currency: str | None) -> str | None:
    """Пометка изменения цены относительно прошлой записи в истории.

    Возвращает, например, '🔺 +7 582 ₽, +14%' (подорожало) или
    '🔻 -2 012 ₽, -7%' (подешевело), 'без изменений' при равенстве цен, либо
    None — если сравнивать не с чем (первая проверка маршрута).
    """
    if not previous:
        return None
    old = previous["price"]
    diff = price - old
    symbol = "₽" if (currency or "rub").lower() == "rub" else (currency or "")
    if diff == 0:
        return "без изменений"
    pct = round(diff / old * 100) if old else 0
    arrow = "🔺" if diff > 0 else "🔻"
    amount = f"{diff:+,}".replace(",", " ")
    return f"{arrow} {amount} {symbol}, {pct:+d}%".strip()


def _stops_label(stops: int | None) -> str | None:
    """0 -> 'прямой', 1 -> '1 пересадка', 2 -> '2 пересадки', 5 -> '5 пересадок'.
    None -> None (число пересадок неизвестно — подпись не показываем)."""
    if stops is None:
        return None
    if stops == 0:
        return "прямой"
    n = stops % 100
    if 11 <= n <= 14:
        word = "пересадок"
    elif stops % 10 == 1:
        word = "пересадка"
    elif stops % 10 in (2, 3, 4):
        word = "пересадки"
    else:
        word = "пересадок"
    return f"{stops} {word}"


def build_message(record: dict, previous: dict | None) -> str:
    """Собрать текст уведомления о новой цене."""
    price = record["price"]
    currency = record.get("currency")

    price_line = f"💰 {_format_price(price, currency)}"
    stops_label = _stops_label(record.get("stops"))
    if stops_label:
        price_line += f" · {stops_label}"
    if previous:
        old = previous["price"]
        diff_pct = round((price - old) / old * 100) if old else 0
        price_line += f" (было: {_format_price(old, currency)}, {diff_pct:+d}%)"

    lines = [
        f"✈️ Новая цена: {route_label(record['origin'], record['destination'])}",
        f"📅 {format_date_ru(record['depart_date'])}",
        price_line,
        f"🏢 {airline_name(record.get('airline'))}",
    ]
    if record.get("link"):
        lines.append(f"🔗 {record['link']}")
    return "\n".join(lines)


def build_status_line(route: dict, record: dict | None, previous: dict | None = None) -> str:
    """Блок с текущей ценой по одному маршруту (для команды /check).

    previous — прошлая запись по маршруту; если передана, к цене добавляется
    пометка изменения (насколько и в какую сторону подорожало/подешевело).
    """
    if record is None:
        return (
            f"✈️ {route_label(route['origin'], route['destination'])}\n"
            f"📅 {format_date_ru(route['depart_date'])}\n"
            f"❌ нет предложений"
        )
    price_line = f"💰 {_format_price(record['price'], record.get('currency'))}"
    stops_label = _stops_label(record.get("stops"))
    if stops_label:
        price_line += f" · {stops_label}"
    change = _format_change(record["price"], previous, record.get("currency"))
    if change:
        price_line += f" ({change})"
    lines = [
        f"✈️ {route_label(record['origin'], record['destination'])}",
        f"📅 {format_date_ru(record['depart_date'])}",
        price_line,
        f"🏢 {airline_name(record.get('airline'))}",
    ]
    if record.get("link"):
        lines.append(f"🔗 {record['link']}")
    return "\n".join(lines)


def build_current_report(items: list[tuple[dict, dict | None, dict | None]]) -> str:
    """Собрать сводку текущих цен по всем маршрутам.

    items — список (route, record, previous); previous нужен, чтобы показать
    изменение цены относительно прошлой проверки.
    """
    header = "📊 Текущие цены по запросу:"
    blocks = [build_status_line(route, record, previous) for route, record, previous in items]
    return header + "\n\n" + "\n\n".join(blocks)


async def _send_async(bot_token: str, chat_id: str, message: str) -> None:
    bot = Bot(token=bot_token)
    async with bot:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


def send_notification(bot_token: str, chat_id: str, message: str) -> None:
    """Отправить сообщение в Telegram (ошибки не крашат процесс)."""
    try:
        asyncio.run(_send_async(bot_token, chat_id, message))
        logger.info("Уведомление отправлено в чат %s", chat_id)
    except TelegramError as exc:
        logger.error("Ошибка отправки в Telegram: %s", exc)
    except Exception as exc:  # noqa: BLE001 — не роняем цикл мониторинга
        logger.error("Непредвиденная ошибка отправки уведомления: %s", exc)
