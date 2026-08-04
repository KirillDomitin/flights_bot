"""Мастер /menu: добавление и удаление отслеживаемых маршрутов.

Пошаговый ConversationHandler: откуда → куда → дата (inline-календарь) → тип
рейса (прямой/с пересадками) → подтверждение. Плюс список маршрутов с удалением.
Города ищем через автоподсказку (`sources/places.py`). Состояние копится в
`context.user_data` и работает на пользователя (в т.ч. в группе).
"""
from __future__ import annotations

import asyncio
import calendar as _calendar
import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from flight_monitor import notifier
from flight_monitor.sources import places

logger = logging.getLogger(__name__)

# Состояния мастера
ORIGIN, DEST, DATE, TYPE, STOPS, PASS, CONFIRM = range(7)

_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
    7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


# --- Inline-календарь ---

def _calendar_markup(year: int, month: int) -> InlineKeyboardMarkup:
    """Клавиатура-календарь на месяц. Прошлые дни и дни соседних месяцев —
    неактивны (cal:ignore); листать назад раньше текущего месяца нельзя."""
    today = date.today()
    rows = []

    prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    can_prev = (year, month) > (today.year, today.month)
    prev_btn = (
        InlineKeyboardButton("‹", callback_data=f"cal:nav:{prev_y}-{prev_m:02d}")
        if can_prev
        else InlineKeyboardButton(" ", callback_data="cal:ignore")
    )
    rows.append([
        prev_btn,
        InlineKeyboardButton(f"{_MONTHS[month]} {year}", callback_data="cal:ignore"),
        InlineKeyboardButton("›", callback_data=f"cal:nav:{next_y}-{next_m:02d}"),
    ])
    rows.append([InlineKeyboardButton(d, callback_data="cal:ignore") for d in _WEEKDAYS])

    for week in _calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for day in week:
            if day.month != month or day < today:
                row.append(InlineKeyboardButton(" ", callback_data="cal:ignore"))
            else:
                row.append(
                    InlineKeyboardButton(str(day.day), callback_data=f"cal:day:{day.isoformat()}")
                )
        rows.append(row)
    return InlineKeyboardMarkup(rows)


# --- Главное меню и список маршрутов (вне мастера) ---

def _main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить перелёт", callback_data="menu:add")],
        [InlineKeyboardButton("📋 Мои перелёты", callback_data="menu:list")],
    ])


async def cmd_menu(update, context) -> None:
    """Команда /menu — главное меню отслеживания."""
    await update.message.reply_text(
        "Меню отслеживания перелётов:", reply_markup=_main_menu_markup()
    )


async def menu_back(update, context) -> None:
    """Callback menu:back — вернуться в главное меню."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Меню отслеживания перелётов:", reply_markup=_main_menu_markup()
    )


def _stops_text(direct_only: bool, stops_wanted: int) -> str:
    """Человекочитаемый тип рейса: 'прямой' или 'ровно 1 пересадка'."""
    if direct_only:
        return "прямой"
    return f"ровно {notifier._stops_label(stops_wanted or 0)}"


def _route_line(r: dict) -> str:
    line = (
        f"{r['origin']}→{r['destination']} · {notifier.format_date_ru(r['depart_date'])}"
        f" · {_stops_text(r['direct_only'], r.get('stops_wanted') or 0)}"
    )
    pax = notifier._passengers_label(r.get("passengers"))
    if pax:
        line += f" · {pax}"
    return line


def _routes_markup(routes: list[dict]) -> InlineKeyboardMarkup:
    """Список маршрутов (тап → подтверждение удаления) и кнопка «Назад» внизу."""
    rows = [
        [InlineKeyboardButton(f"🗑 {_route_line(r)}", callback_data=f"route:ask:{r['id']}")]
        for r in routes
    ]
    rows.append([InlineKeyboardButton("⬅ Назад", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def _list_text(routes: list[dict]) -> str:
    return (
        "Отслеживаемые перелёты (нажмите на перелёт, чтобы удалить):"
        if routes else "Пока нет отслеживаемых перелётов. Добавьте через ➕."
    )


async def menu_list(update, context) -> None:
    """Callback menu:list — показать маршруты с кнопками удаления."""
    query = update.callback_query
    await query.answer()
    routes = context.bot_data["config"]["db"].get_active_routes()
    await query.edit_message_text(_list_text(routes), reply_markup=_routes_markup(routes))


async def route_ask(update, context) -> None:
    """Callback route:ask:<id> — спросить подтверждение удаления маршрута."""
    query = update.callback_query
    await query.answer()
    route_id = int(query.data.split(":")[2])
    routes = context.bot_data["config"]["db"].get_active_routes()
    route = next((r for r in routes if r["id"] == route_id), None)
    if route is None:  # уже удалён кем-то — просто показываем актуальный список
        await query.edit_message_text(_list_text(routes), reply_markup=_routes_markup(routes))
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"route:del:{route_id}")],
        [InlineKeyboardButton("⬅ Отмена", callback_data="menu:list")],
    ])
    await query.edit_message_text(f"Удалить перелёт?\n\n{_route_line(route)}", reply_markup=kb)


async def route_del(update, context) -> None:
    """Callback route:del:<id> — удалить маршрут (после подтверждения) и обновить список."""
    query = update.callback_query
    route_id = int(query.data.split(":")[2])
    repo = context.bot_data["config"]["db"]
    removed = repo.remove_route(route_id)
    routes = repo.get_active_routes()
    await query.answer("Удалено" if removed else "Уже удалён")
    await query.edit_message_text(_list_text(routes), reply_markup=_routes_markup(routes))


# --- Мастер добавления ---

async def add_start(update, context) -> int:
    """Entry: callback menu:add — спросить пункт вылета."""
    query = update.callback_query
    await query.answer()
    context.user_data["new_route"] = {}
    # По умолчанию Москва (кандидат с индексом 0), либо ввод города текстом
    context.user_data["cand"] = [{"code": "MOW", "name": "Москва", "country": "Россия", "type": "city"}]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏙 Москва (MOW)", callback_data="pick:origin:0")]])
    await query.edit_message_text(
        "Откуда летим? Напишите город или выберите:", reply_markup=kb
    )
    return ORIGIN


async def _show_candidates(update, kind: str, term: str, context) -> None:
    """Найти города по тексту и показать кнопки выбора (kind = origin|dest)."""
    found = await asyncio.to_thread(places.search_places, term)
    if not found:
        await update.message.reply_text("Ничего не нашлось — попробуйте другое название города.")
        return
    context.user_data["cand"] = found
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(places.label(p), callback_data=f"pick:{kind}:{i}")]
        for i, p in enumerate(found)
    ])
    await update.message.reply_text("Выберите вариант:", reply_markup=kb)


async def origin_text(update, context) -> int:
    await _show_candidates(update, "origin", update.message.text, context)
    return ORIGIN


async def origin_picked(update, context) -> int:
    query = update.callback_query
    await query.answer()
    place = context.user_data["cand"][int(query.data.split(":")[2])]
    nr = context.user_data["new_route"]
    nr["origin"], nr["origin_name"] = place["code"], place["name"]
    await query.edit_message_text(
        f"Откуда: {place['name']} ({place['code']})\n\nКуда летим? Напишите город:"
    )
    return DEST


async def dest_text(update, context) -> int:
    await _show_candidates(update, "dest", update.message.text, context)
    return DEST


async def dest_picked(update, context) -> int:
    query = update.callback_query
    await query.answer()
    place = context.user_data["cand"][int(query.data.split(":")[2])]
    nr = context.user_data["new_route"]
    nr["destination"], nr["destination_name"] = place["code"], place["name"]
    today = date.today()
    await query.edit_message_text(
        f"{nr['origin_name']} → {place['name']}\n\nВыберите дату вылета:",
        reply_markup=_calendar_markup(today.year, today.month),
    )
    return DATE


async def calendar_nav(update, context) -> int:
    query = update.callback_query
    await query.answer()
    year, month = map(int, query.data.split(":")[2].split("-"))
    await query.edit_message_reply_markup(reply_markup=_calendar_markup(year, month))
    return DATE


async def calendar_ignore(update, context) -> int:
    await update.callback_query.answer()
    return DATE


async def calendar_day(update, context) -> int:
    query = update.callback_query
    await query.answer()
    iso = query.data.split(":")[2]
    context.user_data["new_route"]["depart_date"] = iso
    nr = context.user_data["new_route"]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✈ Только прямой", callback_data="type:direct"),
        InlineKeyboardButton("🔁 Можно с пересадками", callback_data="type:any"),
    ]])
    await query.edit_message_text(
        f"{nr['origin_name']} → {nr['destination_name']} · {notifier.format_date_ru(iso)}"
        "\n\nТип рейса:",
        reply_markup=kb,
    )
    return TYPE


def _wizard_summary(nr: dict) -> str:
    """Сводка собираемого маршрута по человеческим названиям городов."""
    line = (
        f"{nr['origin_name']} → {nr['destination_name']}"
        f" · {notifier.format_date_ru(nr['depart_date'])}"
        f" · {_stops_text(nr.get('direct_only', True), nr.get('stops_wanted') or 0)}"
    )
    pax = notifier._passengers_label(nr.get("passengers"))
    if pax:
        line += f" · {pax}"
    return line


async def _ask_passengers(query, nr: dict) -> int:
    """Спросить число пассажиров (взрослых) — общий шаг для обоих типов рейса."""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(str(n), callback_data=f"pax:{n}") for n in (1, 2, 3, 4)
    ]])
    await query.edit_message_text(
        _wizard_summary(nr) + "\n\nСколько пассажиров (взрослых)?", reply_markup=kb
    )
    return PASS


async def type_picked(update, context) -> int:
    query = update.callback_query
    await query.answer()
    direct = query.data.endswith(":direct")
    nr = context.user_data["new_route"]
    nr["direct_only"] = direct
    if direct:
        nr["stops_wanted"] = 0
        return await _ask_passengers(query, nr)
    # С пересадками → спрашиваем точное число пересадок
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{n}", callback_data=f"stops:{n}") for n in (1, 2, 3)
    ]])
    await query.edit_message_text(
        f"{nr['origin_name']} → {nr['destination_name']}"
        f" · {notifier.format_date_ru(nr['depart_date'])}"
        "\n\nСколько пересадок нужно? (будем искать рейсы ровно с таким числом)",
        reply_markup=kb,
    )
    return STOPS


async def stops_picked(update, context) -> int:
    query = update.callback_query
    await query.answer()
    nr = context.user_data["new_route"]
    nr["stops_wanted"] = int(query.data.split(":")[1])
    return await _ask_passengers(query, nr)


async def passengers_picked(update, context) -> int:
    query = update.callback_query
    await query.answer()
    nr = context.user_data["new_route"]
    nr["passengers"] = int(query.data.split(":")[1])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить", callback_data="add:confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="add:cancel"),
    ]])
    await query.edit_message_text(
        _wizard_summary(nr) + "\n\nДобавить в отслеживание?", reply_markup=kb
    )
    return CONFIRM


async def confirm_yes(update, context) -> int:
    query = update.callback_query
    await query.answer()
    nr = context.user_data["new_route"]
    user = update.effective_user
    added_by = (user.username or user.first_name) if user else None
    context.bot_data["config"]["db"].add_route(
        nr["origin"], nr["destination"], nr["depart_date"],
        nr.get("direct_only", True), nr.get("stops_wanted", 0),
        nr.get("passengers", 1), added_by=added_by,
    )
    await query.edit_message_text("✅ Добавлено: " + _wizard_summary(nr))
    _clear(context)
    return ConversationHandler.END


async def cancel(update, context) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Отменено.")
    elif update.message:
        await update.message.reply_text("Отменено.")
    _clear(context)
    return ConversationHandler.END


def _clear(context) -> None:
    context.user_data.pop("new_route", None)
    context.user_data.pop("cand", None)


def register(application) -> None:
    """Зарегистрировать команду /menu, мастер и обработчики списка/удаления."""
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern="^menu:add$")],
        states={
            ORIGIN: [
                CallbackQueryHandler(origin_picked, pattern="^pick:origin:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, origin_text),
            ],
            DEST: [
                CallbackQueryHandler(dest_picked, pattern="^pick:dest:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, dest_text),
            ],
            DATE: [
                CallbackQueryHandler(calendar_nav, pattern="^cal:nav:"),
                CallbackQueryHandler(calendar_day, pattern="^cal:day:"),
                CallbackQueryHandler(calendar_ignore, pattern="^cal:ignore$"),
            ],
            TYPE: [CallbackQueryHandler(type_picked, pattern="^type:")],
            STOPS: [CallbackQueryHandler(stops_picked, pattern="^stops:")],
            PASS: [CallbackQueryHandler(passengers_picked, pattern="^pax:")],
            CONFIRM: [
                CallbackQueryHandler(confirm_yes, pattern="^add:confirm$"),
                CallbackQueryHandler(cancel, pattern="^add:cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        # брошенный мастер закроется сам через 5 минут (не перехватывает чат)
        conversation_timeout=300,
    )
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(conv)
    application.add_handler(CallbackQueryHandler(menu_list, pattern="^menu:list$"))
    application.add_handler(CallbackQueryHandler(menu_back, pattern="^menu:back$"))
    application.add_handler(CallbackQueryHandler(route_ask, pattern="^route:ask:"))
    application.add_handler(CallbackQueryHandler(route_del, pattern="^route:del:"))
