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


def _build_link(origin: str, destination: str, depart_date: str, passengers: int = 1) -> str:
    """Собрать поисковую ссылку Aviasales вида MOW2209PEK1 (последняя цифра —
    число взрослых пассажиров)."""
    try:
        dt = datetime.strptime(depart_date, "%Y-%m-%d")
        ddmm = dt.strftime("%d%m")
    except ValueError:
        ddmm = ""
    return f"https://www.aviasales.ru/search/{origin}{ddmm}{destination}{max(1, passengers)}"


def fetch_price(
    token: str,
    origin: str,
    destination: str,
    depart_date: str,
    currency: str = "rub",
    direct_only: bool = True,
    stops_wanted: int = 0,
    passengers: int = 1,
) -> Optional[dict]:
    """
    Запросить минимальную цену по маршруту на дату из кэша Travelpayouts.

    direct_only=True  — только прямые рейсы (`/v1/prices/direct`, stops=0);
    direct_only=False — среди `/v1/prices/cheap` берём самый дешёвый рейс
                        РОВНО с stops_wanted пересадками (если таких нет — None).

    passengers в этом источнике на цену не влияет (Data API отдаёт цену за 1
    билет) — параметр принимается для единообразия и попадает в ссылку.

    Возвращает dict (origin/destination/depart_date/price/airline/flight_number/
    stops/passengers/link/currency) или None, если данных нет либо ошибка сети
    (не крашим процесс — логируем и идём дальше).
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

    offers = list(payload.get("data", {}).get(destination, {}).values())
    if not offers:
        logger.info("Нет предложений %s→%s на %s", origin, destination, depart_date)
        return None

    # Не-прямой с точным числом — оставляем предложения РОВНО с N пересадками.
    # stops_wanted == 0 у не-прямого = legacy «любое число» → не фильтруем.
    if not direct_only and stops_wanted:
        offers = [o for o in offers if o.get("number_of_changes") == stops_wanted]
        if not offers:
            logger.info(
                "Нет предложений %s→%s ровно с %d пересадками на %s",
                origin, destination, stops_wanted, depart_date,
            )
            return None

    # берём минимальную цену среди подходящих
    best = min(offers, key=lambda offer: offer.get("price", float("inf")))
    price = best.get("price")
    if price is None:
        logger.info("В предложении отсутствует цена %s→%s", origin, destination)
        return None

    # Число пересадок: direct → 0; ровно N → N; иначе (любое) — из ответа.
    if direct_only:
        stops = 0
    elif stops_wanted:
        stops = stops_wanted
    else:
        stops = best.get("number_of_changes")

    record = {
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "price": int(price),
        "airline": best.get("airline"),
        "flight_number": best.get("flight_number"),
        "stops": stops,
        "passengers": passengers,
        "link": _build_link(origin, destination, depart_date, passengers),
        "currency": payload.get("currency", currency),
    }
    logger.info(
        "Получена цена (%s) %s→%s: %s %s",
        "прямой" if direct_only else "с пересадками",
        origin, destination, record["price"], record["currency"],
    )
    return record
