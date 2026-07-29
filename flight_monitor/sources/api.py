"""Запросы к Travelpayouts Data API (запасной источник цен)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com"
DIRECT_ENDPOINT = "/v1/prices/direct"   # только прямые рейсы
CHEAP_ENDPOINT = "/v1/prices/cheap"     # самые дешёвые, пересадки допускаются
TIMEOUT = 20.0


def _build_link(origin: str, destination: str, depart_date: str) -> str:
    """Собрать поисковую ссылку Aviasales вида MOW2209PEK1."""
    try:
        dt = datetime.strptime(depart_date, "%Y-%m-%d")
        ddmm = dt.strftime("%d%m")
    except ValueError:
        ddmm = ""
    return f"https://www.aviasales.ru/search/{origin}{ddmm}{destination}1"


def fetch_price(
    token: str,
    origin: str,
    destination: str,
    depart_date: str,
    currency: str = "rub",
    direct_only: bool = True,
) -> Optional[dict]:
    """
    Запросить минимальную цену по маршруту на дату из кэша Travelpayouts.

    direct_only=True  — только прямые рейсы (`/v1/prices/direct`, stops=0);
    direct_only=False — самые дешёвые с пересадками (`/v1/prices/cheap`).

    Возвращает dict (origin/destination/depart_date/price/airline/flight_number/
    stops/link/currency) или None, если данных нет либо ошибка сети (не крашим
    процесс — логируем и идём дальше).
    """
    endpoint = DIRECT_ENDPOINT if direct_only else CHEAP_ENDPOINT
    params = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "currency": currency,
    }
    headers = {"X-Access-Token": token}

    try:
        response = httpx.get(
            f"{BASE_URL}{endpoint}",
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

    # Число пересадок: для direct — 0; для cheap — из поля ответа, если оно есть
    stops = 0 if direct_only else best.get("number_of_changes")

    record = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "price": int(price),
        "airline": best.get("airline"),
        "flight_number": best.get("flight_number"),
        "stops": stops,
        "link": _build_link(origin, destination, depart_date),
        "currency": payload.get("currency", currency),
    }
    logger.info(
        "Получена цена (%s) %s→%s: %s %s",
        "прямой" if direct_only else "с пересадками",
        origin, destination, record["price"], record["currency"],
    )
    return record
