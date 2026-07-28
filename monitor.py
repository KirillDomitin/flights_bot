"""Точка входа: мониторинг цен и планировщик.

Маршрут (open-jaw):
    MOW → PEK  22 сентября 2026
    SHA → MOW  30 сентября 2026
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

import schedule
from dotenv import load_dotenv

import api_client
import browser_client
import notifier
import storage

# Консоль Windows по умолчанию не в UTF-8 — принудительно переключаем,
# иначе стрелки и эмодзи в выводе/логах роняют процесс (UnicodeEncodeError).
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
logger = logging.getLogger("monitor")

# Отслеживаемые перелёты
ROUTES = [
    {"origin": "MOW", "destination": "PEK", "depart_date": "2026-09-22"},
    {"origin": "SHA", "destination": "MOW", "depart_date": "2026-09-30"},
]

CURRENCY = "rub"

# Источник цен: "browser" — парсинг Aviasales через Playwright (актуальные цены),
# "api" — кэш Travelpayouts Data API (быстрее, но данные устаревают).
DEFAULT_PRICE_SOURCE = "browser"


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
    return config


def fetch_price(config: dict, route: dict) -> dict | None:
    """Получить самый дешёвый прямой рейс из выбранного источника (browser/api)."""
    if config["price_source"] == "api":
        return api_client.fetch_direct_price(
            token=config["travelpayouts_token"],
            origin=route["origin"],
            destination=route["destination"],
            depart_date=route["depart_date"],
            currency=CURRENCY,
        )
    return browser_client.fetch_cheapest_direct(
        route["origin"],
        route["destination"],
        route["depart_date"],
        headless=config["headless"],
    )


def check_route(conn, config: dict, route: dict) -> str | None:
    """Проверить одну связку: запросить самый дешёвый прямой рейс, сравнить с
    прошлой ценой, сохранить. Вернуть текст уведомления, если цена снизилась
    (или это первый запуск), иначе None. Само сообщение здесь не отправляется —
    его шлёт вызывающая сторона (CLI/schedule или бот через свой event loop).
    """
    record = fetch_price(config, route)
    if record is None:
        return None

    previous = storage.get_last_price(
        conn, route["origin"], route["destination"], route["depart_date"]
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

    storage.save_price(conn, record)
    return message


def collect_check_messages(config: dict) -> list[str]:
    """Проверить все маршруты и вернуть список уведомлений к отправке."""
    logger.info("=== Проверка цен ===")
    conn = storage.get_connection()
    messages: list[str] = []
    try:
        for route in ROUTES:
            try:
                message = check_route(conn, config, route)
                if message:
                    messages.append(message)
            except Exception as exc:  # noqa: BLE001 — один маршрут не рушит остальные
                logger.error(
                    "Ошибка обработки %s→%s: %s",
                    route["origin"],
                    route["destination"],
                    exc,
                )
    finally:
        conn.close()
    logger.info("=== Проверка завершена ===")
    return messages


def run_check(config: dict) -> None:
    """Разовый цикл проверки (CLI --now / schedule): сам шлёт уведомления."""
    for message in collect_check_messages(config):
        notifier.send_notification(
            config["telegram_bot_token"], config["telegram_chat_id"], message
        )


def show_history() -> None:
    """Вывести историю цен из БД в консоль."""
    conn = storage.get_connection()
    try:
        for route in ROUTES:
            print(f"\n{route['origin']} → {route['destination']} ({route['depart_date']}):")
            history = storage.get_history(
                conn, route["origin"], route["destination"], limit=20
            )
            if not history:
                print("  (нет данных)")
                continue
            for row in history:
                print(
                    f"  {row['ts']}  {row['price']:>8} {row['currency'] or ''}"
                    f"  {row['airline'] or '—'}"
                )
    finally:
        conn.close()


def run_scheduler(config: dict) -> None:
    """Запустить блокирующий планировщик (09:00 и 21:00)."""
    schedule.every().day.at("09:00").do(run_check, config=config)
    schedule.every().day.at("21:00").do(run_check, config=config)
    logger.info("Планировщик запущен: проверки в 09:00 и 21:00. Ctrl+C для выхода.")

    # Первичная проверка сразу при старте
    run_check(config)

    while True:
        schedule.run_pending()
        time.sleep(30)


def _fetch_current_prices_sync(config: dict) -> list[tuple[dict, dict | None]]:
    """Синхронно запросить цены по всем маршрутам и сохранить их в БД.

    Соединение SQLite создаётся и используется в одном потоке (иначе sqlite3
    ругается). Возвращает список (route, record|None).
    """
    conn = storage.get_connection()
    items: list[tuple[dict, dict | None]] = []
    try:
        for route in ROUTES:
            record = fetch_price(config, route)
            if record is not None:
                storage.save_price(conn, record)
            items.append((route, record))
    finally:
        conn.close()
    return items


async def _fetch_current_prices(config: dict) -> list[tuple[dict, dict | None]]:
    """Обёртка: выполняет синхронный запрос+сохранение в отдельном потоке,
    чтобы не блокировать event loop бота."""
    return await asyncio.to_thread(_fetch_current_prices_sync, config)


async def cmd_start(update, context) -> None:
    """Ответ на /start — краткая подсказка."""
    await update.message.reply_text(
        "Привет! Я слежу за ценами на билеты MOW→PEK и SHA→MOW.\n"
        "Команда /check — запросить актуальные цены и опубликовать их в группе."
    )


async def cmd_check(update, context) -> None:
    """Ответ на /check — запросить цены и отправить сводку в группу."""
    config = context.bot_data["config"]
    user = update.effective_user.first_name if update.effective_user else "кто-то"
    logger.info("Команда /check от %s (chat %s)", user, update.effective_chat.id)

    await update.message.reply_text("Запрашиваю актуальные цены…")
    try:
        items = await _fetch_current_prices(config)
        text = notifier.build_current_report(items)
        await context.bot.send_message(
            chat_id=config["telegram_chat_id"],
            text=text,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # noqa: BLE001 — не роняем бота из-за одной команды
        logger.error("Ошибка обработки /check: %s", exc)
        await update.message.reply_text("Не удалось получить цены, попробуйте позже.")


async def job_monitor(context) -> None:
    """Плановая проверка цен из JobQueue: шлёт уведомления о снижении в группу."""
    config = context.bot_data["config"]
    messages = await asyncio.to_thread(collect_check_messages, config)
    for message in messages:
        await context.bot.send_message(
            chat_id=config["telegram_chat_id"],
            text=message,
            disable_web_page_preview=True,
        )


def run_bot(config: dict) -> None:
    """Запустить бота: команда /check + плановые проверки 09:00/21:00 в одном
    процессе (через встроенный JobQueue)."""
    import datetime as dtm
    from zoneinfo import ZoneInfo

    from telegram.ext import Application, CommandHandler, Defaults

    # Таймзона для расписания JobQueue (Москва, без перехода на летнее время)
    defaults = Defaults(tzinfo=ZoneInfo("Europe/Moscow"))
    application = (
        Application.builder()
        .token(config["telegram_bot_token"])
        .defaults(defaults)
        .build()
    )
    application.bot_data["config"] = config
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("check", cmd_check))

    if application.job_queue is None:
        logger.warning(
            "JobQueue недоступен — установите python-telegram-bot[job-queue]. "
            "Плановые проверки отключены, работает только команда /check."
        )
    else:
        application.job_queue.run_daily(job_monitor, time=dtm.time(hour=9, minute=0))
        application.job_queue.run_daily(job_monitor, time=dtm.time(hour=21, minute=0))
        logger.info("Плановые проверки включены: 09:00 и 21:00 (Europe/Moscow).")

    logger.info(
        "Бот запущен (polling). /check@ИмяБота — запросить цены. Ctrl+C для выхода."
    )
    application.run_polling(allowed_updates=["message"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Мониторинг цен на авиабилеты")
    parser.add_argument(
        "--now", action="store_true", help="Разовая проверка цен прямо сейчас"
    )
    parser.add_argument(
        "--history", action="store_true", help="Показать историю цен из БД"
    )
    parser.add_argument(
        "--bot",
        action="store_true",
        help="Запустить бота, отвечающего на команду /check в Telegram",
    )
    args = parser.parse_args()

    if args.history:
        show_history()
        return

    config = load_config()

    if args.now:
        run_check(config)
        return

    if args.bot:
        try:
            run_bot(config)
        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем.")
        return

    try:
        run_scheduler(config)
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")
        sys.exit(0)


if __name__ == "__main__":
    main()
