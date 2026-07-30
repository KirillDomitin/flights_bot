"""Конфигурация приложения и настройка логирования.

Здесь живут константы (маршруты, валюта, источник цен по умолчанию), загрузка
`.env` и функция настройки логирования/UTF-8-вывода.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Mapping

from dotenv import load_dotenv

from flight_monitor.repository import cache as cache_module
from flight_monitor.repository import storage

# Маршруты по умолчанию — ими сидится таблица routes при первом запуске
# (open-jaw: MOW→PEK 22.09, SHA→MOW 30.09; оба — только прямые рейсы).
# Дальше маршрутами управляют через меню бота, а не через эту константу.
DEFAULT_ROUTES = [
    {"origin": "MOW", "destination": "PEK", "depart_date": "2026-09-22", "direct_only": True},
    {"origin": "SHA", "destination": "MOW", "depart_date": "2026-09-30", "direct_only": True},
]

CURRENCY = "rub"

# Часы плановых проверок цен (Europe/Moscow) — 4 раза в сутки, каждые 6 часов
CHECK_HOURS = (3, 9, 15, 21)

# Источник цен по умолчанию: "browser" — парсинг Aviasales через Playwright
# (актуальные цены), "api" — кэш Travelpayouts Data API (быстрее, но устаревает).
DEFAULT_PRICE_SOURCE = "browser"

# Способ получения апдейтов от Telegram:
#   polling — long-polling (исходящие соединения, работает за NAT, по умолчанию)
#   webhook — Telegram шлёт апдейты на публичный HTTPS-эндпоинт (нужен ingress)
DEFAULT_BOT_MODE = "polling"
BOT_MODES = ("polling", "webhook")
# Порт встроенного webhook-сервера внутри контейнера (наружу TLS даёт Cloudflare)
DEFAULT_WEBHOOK_PORT = 8443


def build_webhook_settings(env: Mapping[str, str]) -> dict:
    """Разобрать настройки режима бота из окружения.

    Возвращает {bot_mode, webhook_url, webhook_secret, webhook_port}.
    Падает с понятной ошибкой, если BOT_MODE неизвестен или webhook-режим
    выбран без обязательных WEBHOOK_URL/WEBHOOK_SECRET. Чистая функция (без
    os.environ напрямую) — чтобы тестировать без .env.
    """
    mode = (env.get("BOT_MODE") or DEFAULT_BOT_MODE).strip().lower()
    if mode not in BOT_MODES:
        raise SystemExit(
            f"BOT_MODE должен быть одним из {', '.join(BOT_MODES)}, а не {mode!r}."
        )

    settings = {
        "bot_mode": mode,
        "webhook_url": (env.get("WEBHOOK_URL") or "").strip(),
        "webhook_secret": (env.get("WEBHOOK_SECRET") or "").strip(),
        "webhook_port": int(env.get("WEBHOOK_PORT") or DEFAULT_WEBHOOK_PORT),
    }

    if mode == "webhook":
        missing = [
            name
            for name, key in (("WEBHOOK_URL", "webhook_url"), ("WEBHOOK_SECRET", "webhook_secret"))
            if not settings[key]
        ]
        if missing:
            raise SystemExit(
                "BOT_MODE=webhook требует переменные: " + ", ".join(missing) + "."
            )

    return settings


def setup_logging() -> None:
    """Настроить логирование и UTF-8-вывод консоли.

    Консоль Windows по умолчанию не в UTF-8 — стрелки и эмодзи роняют процесс
    (UnicodeEncodeError), поэтому принудительно переключаем stdout/stderr.
    """
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx логирует полный URL, включая токен бота в ссылках Telegram —
    # приглушаем, чтобы секрет не утекал в консоль/логи.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def load_config() -> dict:
    """Загрузить конфигурацию из .env; упасть с понятной ошибкой, если чего-то нет."""
    load_dotenv()
    source = (os.getenv("PRICE_SOURCE") or DEFAULT_PRICE_SOURCE).strip().lower()
    config = {
        "travelpayouts_token": os.getenv("TRAVELPAYOUTS_TOKEN"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "price_source": source,
        # Для отладки браузера можно показать окно: MONITOR_HEADLESS=false
        "headless": (os.getenv("MONITOR_HEADLESS") or "true").strip().lower() != "false",
        "redis_url": os.getenv("REDIS_URL"),
        "cache_ttl": int(os.getenv("CACHE_TTL_SECONDS") or 900),
    }

    # Токен Travelpayouts нужен только в режиме API
    required = ["telegram_bot_token", "telegram_chat_id"]
    if source == "api":
        required.append("travelpayouts_token")

    missing = [key for key in required if not config[key]]
    if missing:
        raise SystemExit(
            "Не заданы переменные окружения: "
            + ", ".join(name.upper() for name in missing)
            + ". Скопируйте .env.example в .env и заполните."
        )

    # Режим бота (polling/webhook) + параметры webhook — валидируется здесь,
    # чтобы упасть на старте, а не в момент запуска бота.
    config.update(build_webhook_settings(os.environ))

    # Кэш (Redis) — best-effort; None, если REDIS_URL не задан
    config["cache"] = cache_module.build_cache(config["redis_url"])
    # Хранилище (сейчас SQLite) за интерфейсом Repository
    config["db"] = storage.build_repository()
    return config
