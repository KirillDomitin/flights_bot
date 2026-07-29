"""Автоподсказка городов/аэропортов (публичный places2 Travelpayouts, без токена).

Пользователь пишет название города — возвращаем варианты с IATA-кодами, чтобы
выбрать кнопкой в мастере /menu.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

AUTOCOMPLETE_URL = "https://autocomplete.travelpayouts.com/places2"
TIMEOUT = 10.0


def search_places(term: str, limit: int = 6) -> list[dict]:
    """Найти города/аэропорты по подстроке. Вернуть список
    {code, name, country, type}; города идут раньше аэропортов. Ошибки сети не
    пробрасываем — логируем и возвращаем []."""
    term = (term or "").strip()
    if len(term) < 2:
        return []
    try:
        response = httpx.get(
            AUTOCOMPLETE_URL,
            params={"term": term, "locale": "ru", "types[]": ["city", "airport"]},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Автоподсказка не удалась (%s): %s", term, exc)
        return []

    places: list[dict] = []
    for item in data:
        code, name = item.get("code"), item.get("name")
        if not code or not name:
            continue
        places.append({
            "code": code,
            "name": name,
            "country": item.get("country_name") or "",
            "type": item.get("type") or "",
        })
    # города вперёд (город агрегирует все аэропорты), затем аэропорты
    places.sort(key=lambda p: 0 if p["type"] == "city" else 1)
    return places[:limit]


def label(place: dict) -> str:
    """'Пекин, Китай (BJS)' — подпись кнопки выбора."""
    country = f", {place['country']}" if place.get("country") else ""
    return f"{place['name']}{country} ({place['code']})"
