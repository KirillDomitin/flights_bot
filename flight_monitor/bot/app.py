"""Сборка и запуск Telegram-бота: команды + плановые проверки в одном процессе."""
from __future__ import annotations

import datetime as dtm
import logging
from zoneinfo import ZoneInfo

from telegram.ext import Application, CommandHandler, Defaults

from flight_monitor.bot import handlers

logger = logging.getLogger(__name__)


def run_bot(config: dict) -> None:
    """Запустить бота: команды /start /check /chart + плановые проверки
    09:00/21:00 в одном процессе (через встроенный JobQueue)."""
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

    if application.job_queue is None:
        logger.warning(
            "JobQueue недоступен — установите python-telegram-bot[job-queue]. "
            "Плановые проверки отключены, работает только команда /check."
        )
    else:
        application.job_queue.run_daily(handlers.job_monitor, time=dtm.time(hour=9, minute=0))
        application.job_queue.run_daily(handlers.job_monitor, time=dtm.time(hour=21, minute=0))
        logger.info("Плановые проверки включены: 09:00 и 21:00 (Europe/Moscow).")

    logger.info(
        "Бот запущен (polling). /check@ИмяБота — запросить цены. Ctrl+C для выхода."
    )
    application.run_polling(allowed_updates=["message"])
