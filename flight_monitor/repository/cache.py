"""Кэш результатов запроса цены.

Абстракция `Cache` (протокол get/set) отделяет `monitor.py` от конкретного
бэкенда — сейчас это Redis, но при желании его можно заменить любым другим
классом с теми же методами, не трогая остальной код.

Кэш — best-effort: любые ошибки бэкенда логируются и глушатся, вызывающая
сторона в этом случае просто делает прямой запрос. Кэш никогда не роняет бота.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class Cache(Protocol):
    """Интерфейс кэша. Значение — запись о цене (dict). None означает промах."""

    def get(self, key: str) -> Optional[dict]:
        ...

    def set(self, key: str, value: dict, ttl: int) -> None:
        ...


def price_key(source: str, route: dict) -> str:
    """Ключ кэша по маршруту. В ключ входят все параметры, влияющие на цену:
    источник (browser и api дают разные цены), тип рейса (s0 = прямой, sN = ровно
    N пересадок, sany = любое число пересадок) и число пассажиров (pN — за всех)."""
    pax = route.get("passengers", 1)
    if route.get("direct_only", True):
        stops = "s0"
    else:
        sw = route.get("stops_wanted", 0)
        stops = f"s{sw}" if sw else "sany"
    return (
        f"price:{source}:{route['origin']}:{route['destination']}:{route['depart_date']}"
        f":{stops}:p{pax}"
    )


class RedisCache:
    """Бэкенд на Redis (синхронный redis-py). Ошибки соединения не пробрасываем."""

    def __init__(self, url: str) -> None:
        import redis  # локальный импорт: redis нужен только при включённом кэше

        self._error = redis.exceptions.RedisError
        # Короткие таймауты: если Redis недоступен, get/set быстро падают, и
        # вызывающая сторона уходит в прямой запрос, а не висит на соединении.
        self._client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    def get(self, key: str) -> Optional[dict]:
        try:
            raw = self._client.get(key)
        except self._error as exc:
            logger.warning("Redis get не удался (%s): %s", key, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            logger.warning("В кэше повреждённое значение (%s) — игнорируем", key)
            return None

    def set(self, key: str, value: dict, ttl: int) -> None:
        try:
            self._client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except self._error as exc:
            logger.warning("Redis set не удался (%s): %s", key, exc)


class MemoryCache:
    """In-process кэш с TTL. Используется в тестах и для локального запуска без
    Redis; демонстрирует, что бэкенд за интерфейсом `Cache` взаимозаменяем."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> Optional[dict]:
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: dict, ttl: int) -> None:
        self._store[key] = (time.monotonic() + ttl, value)


def build_cache(redis_url: Optional[str]) -> Optional[Cache]:
    """Создать кэш по конфигурации. Нет URL → кэш выключен (None) → прямые запросы.

    Клиент Redis подключается лениво (при первом запросе), поэтому недоступный
    в момент старта Redis не помешает боту запуститься.
    """
    if not redis_url:
        logger.info("REDIS_URL не задан — кэш выключен, работаем прямыми запросами.")
        return None
    try:
        cache = RedisCache(redis_url)
    except ImportError:
        logger.warning("Пакет redis не установлен — кэш выключен. pip install redis")
        return None
    logger.info("Кэш включён: Redis (%s)", redis_url)
    return cache
