"""Бизнес-логика мониторинга цен.

Запрос цены из выбранного источника (с учётом кэша), сравнение с предыдущей
записью, сохранение истории и формирование уведомлений. Без Telegram и CLI —
эти слои (bot/cli) вызывают функции отсюда.
"""
from __future__ import annotations

import logging
import time

import schedule

from flight_monitor import notifier
from flight_monitor.config import CHECK_HOURS, CURRENCY, DEFAULT_ROUTES
from flight_monitor.repository import cache as cache_module
from flight_monitor.repository.storage import Repository
from flight_monitor.sources import api as api_client
from flight_monitor.sources import browser as browser_client

logger = logging.getLogger(__name__)


def ensure_seeded(repo: Repository) -> None:
    """Засеять маршруты по умолчанию при первом запуске (идемпотентно)."""
    repo.seed_routes(DEFAULT_ROUTES)


def _fetch_price_uncached(config: dict, route: dict) -> dict | None:
    """Прямой запрос цены из выбранного источника (browser/api), без кэша.
    Параметры рейса берём из маршрута: direct_only (прямой/с пересадками),
    stops_wanted (ровно N пересадок при не-прямом) и passengers (взрослые)."""
    direct_only = route.get("direct_only", True)
    stops_wanted = route.get("stops_wanted", 0)
    passengers = route.get("passengers", 1)
    if config["price_source"] == "api":
        return api_client.fetch_price(
            token=config["travelpayouts_token"],
            origin=route["origin"],
            destination=route["destination"],
            depart_date=route["depart_date"],
            currency=CURRENCY,
            direct_only=direct_only,
            stops_wanted=stops_wanted,
            passengers=passengers,
        )
    return browser_client.fetch_cheapest(
        route["origin"],
        route["destination"],
        route["depart_date"],
        direct_only=direct_only,
        stops_wanted=stops_wanted,
        passengers=passengers,
        headless=config["headless"],
    )


def fetch_price(
    config: dict,
    route: dict,
    *,
    read_cache: bool = True,
    write_cache: bool = True,
) -> dict | None:
    """Получить самый дешёвый прямой рейс с учётом кэша.

    read_cache — сначала заглянуть в кэш (read-through, путь /check);
    write_cache — записать свежий результат в кэш (перезапись, путь джобы).
    «Нет предложений» (None) в кэш не пишем — такие маршруты проверяем каждый раз.
    """
    cache = config.get("cache")
    key = cache_module.price_key(config["price_source"], route)

    if cache is not None and read_cache:
        cached = cache.get(key)
        if cached is not None:
            logger.info(
                "Кэш-попадание %s→%s (%s ₽)",
                route["origin"], route["destination"], cached.get("price"),
            )
            return cached

    record = _fetch_price_uncached(config, route)

    if cache is not None and write_cache and record is not None:
        cache.set(key, record, config["cache_ttl"])

    return record


def check_route(repo: Repository, config: dict, route: dict) -> str | None:
    """Проверить одну связку: запросить самый дешёвый рейс, сравнить с прошлой
    ценой, сохранить. Вернуть текст уведомления, если цена снизилась (или это
    первый запуск), иначе None. Само сообщение здесь не отправляется — его шлёт
    вызывающая сторона (CLI/schedule или бот через свой event loop).

    Мониторинг (джоба/CLI) всегда делает свежий запрос и перезаписывает кэш —
    read_cache=False, — чтобы гарантированно получить точку истории и отработать
    детект снижения, не завися от того, читал ли кто-то цену недавно через /check.
    """
    record = fetch_price(config, route, read_cache=False)
    if record is None:
        return None

    previous = repo.get_last_price(
        route["origin"], route["destination"], route["depart_date"]
    )

    # Уведомляем при первом запуске или при снижении цены
    is_first = previous is None
    is_cheaper = previous is not None and record["price"] < previous["price"]

    message: str | None = None
    if is_first or is_cheaper:
        message = notifier.build_message(record, previous)
    else:
        logger.info(
            "Цена %s→%s не снизилась (%s ≥ %s) — без уведомления",
            route["origin"],
            route["destination"],
            record["price"],
            previous["price"],
        )

    repo.save_price(record)
    return message


def collect_check_messages(config: dict) -> list[str]:
    """Проверить все маршруты и вернуть список уведомлений к отправке."""
    logger.info("=== Проверка цен ===")
    repo: Repository = config["db"]
    messages: list[str] = []
    for route in repo.get_active_routes():
        try:
            message = check_route(repo, config, route)
            if message:
                messages.append(message)
        except Exception as exc:  # noqa: BLE001 — один маршрут не рушит остальные
            logger.error(
                "Ошибка обработки %s→%s: %s",
                route["origin"],
                route["destination"],
                exc,
            )
    logger.info("=== Проверка завершена ===")
    return messages


def run_check(config: dict) -> None:
    """Разовый цикл проверки (CLI --now / schedule): сам шлёт уведомления."""
    for message in collect_check_messages(config):
        notifier.send_notification(
            config["telegram_bot_token"], config["telegram_chat_id"], message
        )


def show_history(repo: Repository) -> None:
    """Вывести историю цен из БД в консоль."""
    for route in repo.get_active_routes():
        print(f"\n{route['origin']} → {route['destination']} ({route['depart_date']}):")
        history = repo.get_history(route["origin"], route["destination"], limit=20)
        if not history:
            print("  (нет данных)")
            continue
        for row in history:
            print(
                f"  {row['ts']}  {row['price']:>8} {row['currency'] or ''}"
                f"  {row['airline'] or '—'}"
            )


def run_scheduler(config: dict) -> None:
    """Запустить блокирующий планировщик (4 раза в сутки, см. CHECK_HOURS)."""
    for hour in CHECK_HOURS:
        schedule.every().day.at(f"{hour:02d}:00").do(run_check, config=config)
    times = ", ".join(f"{h:02d}:00" for h in CHECK_HOURS)
    logger.info("Планировщик запущен: проверки в %s. Ctrl+C для выхода.", times)

    # Первичная проверка сразу при старте
    run_check(config)

    while True:
        schedule.run_pending()
        time.sleep(30)


def fetch_current_prices(config: dict) -> list[tuple[dict, dict | None, dict | None]]:
    """Синхронно запросить цены по всем маршрутам (read-through кэш) и сохранить.

    Путь команды /check. Возвращает список (route, record|None, previous|None),
    где previous — прошлая запись по маршруту (до этой проверки), нужная, чтобы
    показать изменение цены.
    """
    repo: Repository = config["db"]
    items: list[tuple[dict, dict | None, dict | None]] = []
    for route in repo.get_active_routes():
        record = fetch_price(config, route)
        previous = None
        if record is not None:
            previous = repo.get_last_price(
                route["origin"], route["destination"], route["depart_date"]
            )
            # /check вызывают часто; пишем точку в историю только если цена
            # изменилась — иначе плодятся плоские дубли (регулярную выборку
            # обеспечивает плановая джоба, а не эта команда).
            if previous is None or previous["price"] != record["price"]:
                repo.save_price(record)
        items.append((route, record, previous))
    return items


def build_chart_png(repo: Repository) -> bytes | None:
    """Собрать историю по всем маршрутам из БД и построить график.

    Возвращает PNG в байтах или None, если истории нет вовсе. Импорт chart
    ленивый — matplotlib тяжёлый и не нужен для остальных режимов.
    """
    from flight_monitor import chart

    series = [
        (route, repo.get_route_series(
            route["origin"], route["destination"], route["depart_date"]
        ))
        for route in repo.get_active_routes()
    ]
    return chart.render_price_chart(series)
