"""CLI: разбор аргументов и запуск нужного режима.

Режимы: --now (разовая проверка), --history (история из БД), --chart (график в
файл), --bot (Telegram-бот), без аргументов — блокирующий планировщик.
"""
from __future__ import annotations

import argparse
import logging
import sys

from flight_monitor import config as config_module
from flight_monitor.core import monitoring

logger = logging.getLogger("flight_monitor")


def main() -> None:
    config_module.setup_logging()

    parser = argparse.ArgumentParser(description="Мониторинг цен на авиабилеты")
    parser.add_argument(
        "--now", action="store_true", help="Разовая проверка цен прямо сейчас"
    )
    parser.add_argument(
        "--history", action="store_true", help="Показать историю цен из БД"
    )
    parser.add_argument(
        "--chart",
        nargs="?",
        const="price_chart.png",
        metavar="FILE",
        help="Построить график цен из БД и сохранить в файл (по умолчанию price_chart.png)",
    )
    parser.add_argument(
        "--bot",
        action="store_true",
        help="Запустить бота (команды /check, /chart + плановые проверки)",
    )
    args = parser.parse_args()

    if args.history:
        monitoring.show_history()
        return

    if args.chart:
        png = monitoring.build_chart_png()
        if png is None:
            print("Нет истории цен для графика.")
            return
        with open(args.chart, "wb") as fh:
            fh.write(png)
        print(f"График сохранён: {args.chart}")
        return

    config = config_module.load_config()

    if args.now:
        monitoring.run_check(config)
        return

    if args.bot:
        # Ленивый импорт: telegram.ext нужен только в режиме бота.
        from flight_monitor.bot import app as bot_app

        try:
            bot_app.run_bot(config)
        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем.")
        return

    try:
        monitoring.run_scheduler(config)
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")
        sys.exit(0)
