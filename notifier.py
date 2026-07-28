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


def build_message(record: dict, previous: dict | None) -> str:
    """Собрать текст уведомления о новой цене."""
    price = record["price"]
    currency = "₽" if (record.get("currency", "rub").lower() == "rub") else record.get("currency", "")

    price_line = f"💰 {price:,} {currency}".replace(",", " ")
    if previous:
        old = previous["price"]
        diff_pct = round((price - old) / old * 100) if old else 0
        price_line += f" (было: {old:,} {currency}".replace(",", " ") + f", {diff_pct:+d}%)"

    lines = [
        f"✈️ Новая цена: {record['origin']} → {record['destination']}",
        f"📅 {format_date_ru(record['depart_date'])}",
        price_line,
        f"🏢 {airline_name(record.get('airline'))}",
    ]
    if record.get("link"):
        lines.append(f"🔗 {record['link']}")
    return "\n".join(lines)


def build_status_line(route: dict, record: dict | None) -> str:
    """Блок с текущей ценой по одному маршруту (для команды /check)."""
    if record is None:
        return (
            f"✈️ {route['origin']} → {route['destination']}\n"
            f"📅 {format_date_ru(route['depart_date'])}\n"
            f"❌ нет предложений"
        )
    currency = "₽" if record.get("currency", "rub").lower() == "rub" else record.get("currency", "")
    price = f"{record['price']:,}".replace(",", " ")
    lines = [
        f"✈️ {record['origin']} → {record['destination']}",
        f"📅 {format_date_ru(record['depart_date'])}",
        f"💰 {price} {currency}",
        f"🏢 {airline_name(record.get('airline'))}",
    ]
    if record.get("link"):
        lines.append(f"🔗 {record['link']}")
    return "\n".join(lines)


def build_current_report(items: list[tuple[dict, dict | None]]) -> str:
    """Собрать сводку текущих цен по всем маршрутам."""
    header = "📊 Текущие цены по запросу:"
    blocks = [build_status_line(route, record) for route, record in items]
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
