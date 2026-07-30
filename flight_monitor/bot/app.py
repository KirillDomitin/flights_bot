"""Сборка и запуск Telegram-бота: команды + плановые проверки в одном процессе."""
from __future__ import annotations

import datetime as dtm
import logging
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from telegram.ext import Application, CommandHandler, Defaults

from flight_monitor.bot import handlers, menu
from flight_monitor.config import CHECK_HOURS

logger = logging.getLogger(__name__)

# callback_query нужен для inline-кнопок мастера /menu
_ALLOWED_UPDATES = ["message", "callback_query"]


def _webhook_path(webhook_url: str) -> str:
    """Путь для встроенного webhook-сервера — берём из WEBHOOK_URL, чтобы
    url_path и публичный адрес не разъезжались (единый источник правды)."""
    return urlparse(webhook_url).path.lstrip("/")


def run_bot(config: dict) -> None:
    """Запустить бота: команды /start /check /chart + плановые проверки
    (4 раза в сутки) в одном процессе (через встроенный JobQueue)."""
    # Таймзона для расписания JobQueue (Москва, без перехода на летнее время)
    defaults = Defaults(tzinfo=ZoneInfo("Europe/Moscow"))
    application = (
        Application.builder()
        .token(config["telegram_bot_token"])
        .defaults(defaults)
        .build()
    )
    application.bot_data["config"] = config
    application.add_handler(CommandHandler("start", handlers.cmd_start))
    application.add_handler(CommandHandler("check", handlers.cmd_check))
    application.add_handler(CommandHandler("chart", handlers.cmd_chart))
    # Мастер /menu: добавление/удаление маршрутов (команда + inline-кнопки)
    menu.register(application)

    if application.job_queue is None:
        logger.warning(
            "JobQueue недоступен — установите python-telegram-bot[job-queue]. "
            "Плановые проверки отключены, работает только команда /check."
        )
    else:
        for hour in CHECK_HOURS:
            application.job_queue.run_daily(handlers.job_monitor, time=dtm.time(hour=hour, minute=0))
        times = ", ".join(f"{h:02d}:00" for h in CHECK_HOURS)
        logger.info("Плановые проверки включены: %s (Europe/Moscow).", times)

    if config.get("bot_mode") == "webhook":
        path = _webhook_path(config["webhook_url"])
        logger.info(
            "Бот запущен (webhook): слушаю :%s, публичный адрес %s",
            config["webhook_port"], config["webhook_url"],
        )
        # TLS терминирует Cloudflare (туннель ходит к origin по HTTP) — cert/key
        # тут не нужны. secret_token отсекает фейковые апдейты мимо туннеля.
        # drop_pending_updates — чистый старт при переключении с polling.
        application.run_webhook(
            listen="0.0.0.0",
            port=config["webhook_port"],
            url_path=path,
            secret_token=config["webhook_secret"],
            webhook_url=config["webhook_url"],
            allowed_updates=_ALLOWED_UPDATES,
            drop_pending_updates=True,
        )
        return

    logger.info(
        "Бот запущен (polling). /check@ИмяБота — запросить цены. Ctrl+C для выхода."
    )
    application.run_polling(allowed_updates=_ALLOWED_UPDATES)
