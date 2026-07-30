"""Обработчики команд Telegram-бота (/start, /check, /chart) и плановая джоба.

Тяжёлые синхронные операции (запрос цен, построение графика) выносим в отдельный
поток через asyncio.to_thread, чтобы не блокировать event loop бота.
"""
from __future__ import annotations

import asyncio
import logging

from flight_monitor import notifier
from flight_monitor.core import monitoring

logger = logging.getLogger(__name__)


async def _fetch_current_prices(config: dict) -> list[tuple[dict, dict | None]]:
    """Запрос+сохранение цен в отдельном потоке (не блокируем event loop)."""
    return await asyncio.to_thread(monitoring.fetch_current_prices, config)


async def _render_chart(repo) -> bytes | None:
    """Построение графика в отдельном потоке (не блокируем event loop)."""
    return await asyncio.to_thread(monitoring.build_chart_png, repo)


async def cmd_start(update, context) -> None:
    """Ответ на /start — краткая подсказка."""
    await update.message.reply_text(
        "Привет! Я слежу за ценами на авиабилеты.\n"
        "/check — запросить актуальные цены по отслеживаемым маршрутам\n"
        "/chart — график изменения цены по истории\n"
        "/menu — добавить или убрать отслеживаемый перелёт"
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


async def cmd_chart(update, context) -> None:
    """Ответ на /chart — построить график цен по истории и прислать картинкой."""
    config = context.bot_data["config"]
    user = update.effective_user.first_name if update.effective_user else "кто-то"
    logger.info("Команда /chart от %s (chat %s)", user, update.effective_chat.id)

    await update.message.reply_text("Строю график…")
    try:
        png = await _render_chart(config["db"])
        if png is None:
            await update.message.reply_text(
                "Пока нет истории цен для графика — данные появятся после проверок."
            )
            return
        await context.bot.send_photo(
            chat_id=config["telegram_chat_id"],
            photo=png,
            caption="📈 История цен: MOW→PEK и SHA→MOW",
        )
    except Exception as exc:  # noqa: BLE001 — не роняем бота из-за одной команды
        logger.error("Ошибка обработки /chart: %s", exc)
        await update.message.reply_text("Не удалось построить график, попробуйте позже.")


async def job_monitor(context) -> None:
    """Плановая проверка цен из JobQueue: шлёт уведомления о снижении в группу."""
    config = context.bot_data["config"]
    messages = await asyncio.to_thread(monitoring.collect_check_messages, config)
    for message in messages:
        await context.bot.send_message(
            chat_id=config["telegram_chat_id"],
            text=message,
            disable_web_page_preview=True,
        )
