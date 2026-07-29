"""Запросы к Travelpayouts Data API."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com"
DIRECT_ENDPOINT = "/v1/prices/direct"
TIMEOUT = 20.0


def _build_link(origin: str, destination: str, depart_date: str) -> str:
    """Собрать поисковую ссылку Aviasales вида MOW2209PEK1."""
    try:
        dt = datetime.strptime(depart_date, "%Y-%m-%d")
        ddmm = dt.strftime("%d%m")
    except ValueError:
        ddmm = ""
    return f"https://www.aviasales.ru/search/{origin}{ddmm}{destination}1"


def fetch_direct_price(
    token: str,
    origin: str,
    destination: str,
    depart_date: str,
    currency: str = "rub",
) -> Optional[dict]:
    """
    Запросить минимальную цену прямого рейса по маршруту на дату.

    Возвращает dict с полями origin/destination/depart_date/price/airline/
    flight_number/link/currency или None, если данных нет либо произошла
    ошибка сети (не крашим процесс — логируем и идём дальше).
    """
    params = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "currency": currency,
    }
    headers = {"X-Access-Token": token}

    try:
        response = httpx.get(
            f"{BASE_URL}{DIRECT_ENDPOINT}",
            params=params,
            headers=headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.error("Ошибка запроса %s→%s: %s", origin, destination, exc)
        return None
    except ValueError as exc:  # некорректный JSON
        logger.error("Некорректный ответ API %s→%s: %s", origin, destination, exc)
        return None

    if not payload.get("success"):
        logger.warning("API вернул success=false для %s→%s", origin, destination)
        return None

    offers = payload.get("data", {}).get(destination, {})
    if not offers:
        logger.info("Нет предложений %s→%s на %s", origin, destination, depart_date)
        return None

    # data[destination] — словарь {"0": {...}, "1": {...}}; берём минимальную цену
    best = min(offers.values(), key=lambda offer: offer.get("price", float("inf")))
    price = best.get("price")
    if price is None:
        logger.info("В предложении отсутствует цена %s→%s", origin, destination)
        return None

    record = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "price": int(price),
        "airline": best.get("airline"),
        "flight_number": best.get("flight_number"),
        "link": _build_link(origin, destination, depart_date),
        "currency": payload.get("currency", currency),
    }
    logger.info(
        "Получена цена %s→%s: %s %s",
        origin,
        destination,
        record["price"],
        record["currency"],
    )
    return record
