"""Мок-тесты логики сравнения цен и кэша (без реального API и Telegram).

Запуск из корня репозитория:
    python -m unittest
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flight_monitor import chart, config, notifier
from flight_monitor.bot import app as bot_app
from flight_monitor.bot import menu
from flight_monitor.core import monitoring
from flight_monitor.repository import cache, storage
from flight_monitor.sources import api, browser, places

_CONFIG = {
    "travelpayouts_token": "test-token",
    "telegram_bot_token": "test-bot",
    "telegram_chat_id": "123",
    # тесты мокают источник api → monitoring.api_client.fetch_direct_price
    "price_source": "api",
    "headless": True,
}

_ROUTE = {"origin": "MOW", "destination": "PEK", "depart_date": "2025-09-22"}


def _record(price: int) -> dict:
    return {
        "origin": "MOW",
        "destination": "PEK",
        "depart_date": "2025-09-22",
        "price": price,
        "airline": "SU",
        "flight_number": 200,
        "link": "https://www.aviasales.ru/search/MOW2209PEK1",
        "stops": 0,
        "currency": "rub",
    }


class CheckRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        # Отдельный репозиторий на временной БД для каждого теста
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.repo = storage.SqliteRepository(db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, price: int) -> str | None:
        """Выполнить check_route с замоканным API, вернуть текст уведомления/None."""
        with mock.patch.object(
            monitoring.api_client, "fetch_price", return_value=_record(price)
        ):
            return monitoring.check_route(self.repo, _CONFIG, _ROUTE)

    def test_first_run_notifies_and_saves(self) -> None:
        message = self._run(42100)
        self.assertIsNotNone(message)  # первый запуск → уведомление
        last = self.repo.get_last_price("MOW", "PEK", "2025-09-22")
        self.assertEqual(last["price"], 42100)

    def test_price_drop_notifies(self) -> None:
        self._run(42100)                 # первичная запись
        message = self._run(38500)       # цена ниже
        self.assertIsNotNone(message)
        last = self.repo.get_last_price("MOW", "PEK", "2025-09-22")
        self.assertEqual(last["price"], 38500)

    def test_price_same_does_not_notify(self) -> None:
        self._run(42100)
        self.assertIsNone(self._run(42100))

    def test_price_increase_does_not_notify_but_saves(self) -> None:
        self._run(42100)
        message = self._run(45000)
        self.assertIsNone(message)
        last = self.repo.get_last_price("MOW", "PEK", "2025-09-22")
        self.assertEqual(last["price"], 45000)

    def test_api_none_skips_everything(self) -> None:
        with mock.patch.object(
            monitoring.api_client, "fetch_price", return_value=None
        ):
            message = monitoring.check_route(self.repo, _CONFIG, _ROUTE)
        self.assertIsNone(message)
        self.assertIsNone(self.repo.get_last_price("MOW", "PEK", "2025-09-22"))


class MessageFormatTests(unittest.TestCase):
    def test_diff_percent_in_message(self) -> None:
        msg = notifier.build_message(_record(38500), {"price": 42100})
        self.assertIn("38 500", msg)
        self.assertIn("было: 42 100", msg)
        self.assertIn("-9%", msg)

    def test_format_price_rub_uses_symbol_and_spaces(self) -> None:
        self.assertEqual(notifier._format_price(42100, "rub"), "42 100 ₽")

    def test_format_price_non_rub_uses_code(self) -> None:
        self.assertEqual(notifier._format_price(1500, "usd"), "1 500 usd")

    def test_route_label(self) -> None:
        self.assertEqual(notifier.route_label("MOW", "PEK"), "MOW → PEK")

    def test_status_line_with_offer(self) -> None:
        line = notifier.build_status_line(_ROUTE, _record(27829))
        self.assertIn("MOW → PEK", line)
        self.assertIn("27 829 ₽", line)
        self.assertIn("Аэрофлот", line)  # SU → человекочитаемое имя

    def test_status_line_no_offer(self) -> None:
        line = notifier.build_status_line(_ROUTE, None)
        self.assertIn("нет предложений", line)

    def test_current_report_lists_all_routes(self) -> None:
        report = notifier.build_current_report(
            [(_ROUTE, _record(27829), None), (_ROUTE, None, None)]
        )
        self.assertIn("Текущие цены", report)
        self.assertIn("27 829 ₽", report)
        self.assertIn("нет предложений", report)

    def test_status_line_shows_price_increase(self) -> None:
        line = notifier.build_status_line(_ROUTE, _record(45000), {"price": 42100})
        self.assertIn("🔺", line)
        self.assertIn("+2 900", line)   # 45000 − 42100
        self.assertIn("+7%", line)

    def test_status_line_shows_price_drop(self) -> None:
        line = notifier.build_status_line(_ROUTE, _record(38500), {"price": 42100})
        self.assertIn("🔻", line)
        self.assertIn("-3 600", line)   # 38500 − 42100
        self.assertIn("-9%", line)

    def test_status_line_no_change_marker(self) -> None:
        line = notifier.build_status_line(_ROUTE, _record(42100), {"price": 42100})
        self.assertIn("без изменений", line)

    def test_status_line_no_previous_has_no_marker(self) -> None:
        line = notifier.build_status_line(_ROUTE, _record(42100), None)
        self.assertNotIn("🔺", line)
        self.assertNotIn("🔻", line)
        self.assertNotIn("без изменений", line)

    def test_passengers_label(self) -> None:
        self.assertIsNone(notifier._passengers_label(1))
        self.assertIsNone(notifier._passengers_label(None))
        self.assertEqual(notifier._passengers_label(2), "2 взрослых")
        self.assertEqual(notifier._passengers_label(4), "4 взрослых")

    def test_status_line_shows_passengers(self) -> None:
        line = notifier.build_status_line(_ROUTE, dict(_record(27829), passengers=2))
        self.assertIn("2 взрослых", line)
        # одного пассажира не подписываем
        solo = notifier.build_status_line(_ROUTE, dict(_record(27829), passengers=1))
        self.assertNotIn("взросл", solo)

    def test_stops_label_pluralization(self) -> None:
        self.assertEqual(notifier._stops_label(0), "прямой")
        self.assertEqual(notifier._stops_label(1), "1 пересадка")
        self.assertEqual(notifier._stops_label(2), "2 пересадки")
        self.assertEqual(notifier._stops_label(5), "5 пересадок")
        self.assertEqual(notifier._stops_label(11), "11 пересадок")
        self.assertIsNone(notifier._stops_label(None))

    def test_status_line_shows_stops(self) -> None:
        direct = notifier.build_status_line(_ROUTE, _record(27829))
        self.assertIn("прямой", direct)
        with_stops = notifier.build_status_line(_ROUTE, dict(_record(19989), stops=1))
        self.assertIn("1 пересадка", with_stops)


class CacheTests(unittest.TestCase):
    def test_price_key_includes_stops_and_passengers(self) -> None:
        # _ROUTE без stops_wanted/passengers → s0 (прямой), p1
        self.assertEqual(
            cache.price_key("browser", _ROUTE),
            "price:browser:MOW:PEK:2025-09-22:s0:p1",
        )
        # ровно 1 пересадка и 2 пассажира → другой ключ
        route2 = dict(_ROUTE, direct_only=False, stops_wanted=1, passengers=2)
        self.assertEqual(
            cache.price_key("browser", route2),
            "price:browser:MOW:PEK:2025-09-22:s1:p2",
        )

    def test_memory_cache_hit_and_miss(self) -> None:
        c = cache.MemoryCache()
        self.assertIsNone(c.get("k"))          # промах
        c.set("k", {"price": 1}, ttl=60)
        self.assertEqual(c.get("k"), {"price": 1})  # попадание

    def test_memory_cache_expiry(self) -> None:
        c = cache.MemoryCache()
        with mock.patch.object(cache.time, "monotonic", return_value=1000.0):
            c.set("k", {"price": 1}, ttl=10)   # истекает в 1010
        with mock.patch.object(cache.time, "monotonic", return_value=1011.0):
            self.assertIsNone(c.get("k"))       # уже протух

    def _cfg_with_cache(self) -> dict:
        cfg = dict(_CONFIG)
        cfg["cache"] = cache.MemoryCache()
        cfg["cache_ttl"] = 900
        return cfg

    def test_read_through_fetches_once(self) -> None:
        cfg = self._cfg_with_cache()
        with mock.patch.object(
            monitoring, "_fetch_price_uncached", return_value=_record(100)
        ) as m:
            r1 = monitoring.fetch_price(cfg, _ROUTE)   # промах → запрос + запись
            r2 = monitoring.fetch_price(cfg, _ROUTE)   # попадание → без запроса
        self.assertEqual(m.call_count, 1)
        self.assertEqual(r1["price"], 100)
        self.assertEqual(r2["price"], 100)

    def test_read_cache_false_always_fetches_and_overwrites(self) -> None:
        cfg = self._cfg_with_cache()
        with mock.patch.object(
            monitoring, "_fetch_price_uncached", return_value=_record(100)
        ):
            monitoring.fetch_price(cfg, _ROUTE)                    # кладём 100 в кэш
        # джоба: read_cache=False → игнорирует кэш, перезаписывает свежим 200
        with mock.patch.object(
            monitoring, "_fetch_price_uncached", return_value=_record(200)
        ) as m:
            monitoring.fetch_price(cfg, _ROUTE, read_cache=False)
        self.assertEqual(m.call_count, 1)
        # теперь в кэше 200 — обычный read-through отдаёт его без запроса
        with mock.patch.object(
            monitoring, "_fetch_price_uncached", return_value=_record(999)
        ) as m2:
            self.assertEqual(monitoring.fetch_price(cfg, _ROUTE)["price"], 200)
        self.assertEqual(m2.call_count, 0)

    def test_none_is_not_cached(self) -> None:
        cfg = self._cfg_with_cache()
        with mock.patch.object(
            monitoring, "_fetch_price_uncached", return_value=None
        ) as m:
            monitoring.fetch_price(cfg, _ROUTE)
            monitoring.fetch_price(cfg, _ROUTE)
        self.assertEqual(m.call_count, 2)  # None не кэшируется → всегда запрос


class RoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.repo = storage.SqliteRepository(db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_seed_fills_empty_then_idempotent(self) -> None:
        self.assertEqual(self.repo.get_active_routes(), [])
        self.repo.seed_routes(config.DEFAULT_ROUTES)
        routes = self.repo.get_active_routes()
        self.assertEqual(len(routes), len(config.DEFAULT_ROUTES))
        self.assertEqual(routes[0]["origin"], "MOW")
        self.assertTrue(routes[0]["direct_only"])
        # повторный сид ничего не добавляет
        self.repo.seed_routes(config.DEFAULT_ROUTES)
        self.assertEqual(len(self.repo.get_active_routes()), len(config.DEFAULT_ROUTES))

    def test_add_and_remove_route(self) -> None:
        rid = self.repo.add_route("LED", "AER", "2026-10-01", direct_only=False)
        self.assertIsNotNone(rid)
        routes = self.repo.get_active_routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["destination"], "AER")
        self.assertFalse(routes[0]["direct_only"])
        # удаление → пропадает из активных
        self.assertTrue(self.repo.remove_route(rid))
        self.assertEqual(self.repo.get_active_routes(), [])
        # повторное удаление уже неактивного → False
        self.assertFalse(self.repo.remove_route(rid))

    def test_add_route_stores_stops_and_passengers(self) -> None:
        self.repo.add_route("MOW", "IST", "2026-10-01", direct_only=False, stops_wanted=2, passengers=3)
        r = self.repo.get_active_routes()[0]
        self.assertFalse(r["direct_only"])
        self.assertEqual(r["stops_wanted"], 2)
        self.assertEqual(r["passengers"], 3)

    def test_readd_updates_stops_and_passengers(self) -> None:
        rid = self.repo.add_route("MOW", "IST", "2026-10-01", direct_only=False, stops_wanted=1, passengers=1)
        rid2 = self.repo.add_route("MOW", "IST", "2026-10-01", direct_only=False, stops_wanted=2, passengers=4)
        self.assertEqual(rid, rid2)  # тот же маршрут (UNIQUE), обновлён
        r = self.repo.get_active_routes()[0]
        self.assertEqual(r["stops_wanted"], 2)
        self.assertEqual(r["passengers"], 4)

    def test_add_duplicate_reactivates(self) -> None:
        rid = self.repo.add_route("LED", "AER", "2026-10-01", direct_only=True)
        self.repo.remove_route(rid)
        self.assertEqual(self.repo.get_active_routes(), [])
        # тот же маршрут снова → включается обратно, без дублей
        rid2 = self.repo.add_route("LED", "AER", "2026-10-01", direct_only=True)
        self.assertEqual(rid2, rid)
        self.assertEqual(len(self.repo.get_active_routes()), 1)


class _FakeResp:
    def __init__(self, data) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._data


class PlacesTests(unittest.TestCase):
    def test_search_parses_and_orders_city_first(self) -> None:
        fake = [
            {"code": "PKX", "name": "Пекин Дасин", "country_name": "Китай", "type": "airport"},
            {"code": "BJS", "name": "Пекин", "country_name": "Китай", "type": "city"},
            {"code": "PEK", "name": "Пекин", "country_name": "Китай", "type": "airport"},
        ]
        with mock.patch.object(places.httpx, "get", return_value=_FakeResp(fake)):
            res = places.search_places("Пекин")
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0]["type"], "city")   # город вперёд аэропортов
        self.assertEqual(res[0]["code"], "BJS")

    def test_search_short_term_returns_empty(self) -> None:
        self.assertEqual(places.search_places("П"), [])

    def test_label(self) -> None:
        self.assertEqual(
            places.label({"code": "PEK", "name": "Пекин", "country": "Китай"}),
            "Пекин, Китай (PEK)",
        )


class BrowserSelectTests(unittest.TestCase):
    def test_select_from_list_picks_cheapest_with_exact_stops(self) -> None:
        tickets = [
            {"price": 15000, "stops": 0},
            {"price": 12000, "stops": 2},
            {"price": 18000, "stops": 1},
            {"price": 16000, "stops": 1},  # самый дешёвый ровно с 1 пересадкой
        ]
        best = browser._select_from_list(tickets, 1)
        self.assertEqual(best["price"], 16000)

    def test_select_from_list_none_when_no_exact_match(self) -> None:
        tickets = [{"price": 12000, "stops": 0}, {"price": 15000, "stops": 2}]
        self.assertIsNone(browser._select_from_list(tickets, 1))

    def test_build_search_url_encodes_passengers(self) -> None:
        self.assertTrue(browser.build_search_url("MOW", "PEK", "2026-09-22", 3).endswith("PEK3"))
        self.assertTrue(browser.build_search_url("MOW", "PEK", "2026-09-22").endswith("PEK1"))


class ApiExactStopsTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {
            "success": True,
            "currency": "rub",
            "data": {"PEK": {
                "0": {"price": 20000, "number_of_changes": 0, "airline": "SU"},
                "1": {"price": 15000, "number_of_changes": 1, "airline": "CA"},
                "2": {"price": 12000, "number_of_changes": 2, "airline": "MU"},
            }},
        }

    def test_exact_one_stop_selected(self) -> None:
        with mock.patch.object(api.httpx, "get", return_value=_FakeResp(self._payload())):
            rec = api.fetch_price(
                "t", "MOW", "PEK", "2025-09-22",
                direct_only=False, stops_wanted=1, passengers=2,
            )
        self.assertEqual(rec["price"], 15000)   # не самый дешёвый (12000/2 пересадки), а ровно 1
        self.assertEqual(rec["stops"], 1)
        self.assertEqual(rec["passengers"], 2)

    def test_none_when_no_offer_with_wanted_stops(self) -> None:
        payload = {"success": True, "currency": "rub",
                   "data": {"PEK": {"0": {"price": 20000, "number_of_changes": 0}}}}
        with mock.patch.object(api.httpx, "get", return_value=_FakeResp(payload)):
            rec = api.fetch_price("t", "MOW", "PEK", "2025-09-22", direct_only=False, stops_wanted=1)
        self.assertIsNone(rec)


class CalendarTests(unittest.TestCase):
    def test_future_month_has_selectable_days_and_header(self) -> None:
        markup = menu._calendar_markup(2030, 6)
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("cal:day:2030-06-15", datas)
        self.assertIn("Июнь 2030", markup.inline_keyboard[0][1].text)

    def test_prev_disabled_in_current_month(self) -> None:
        from datetime import date

        today = date.today()
        markup = menu._calendar_markup(today.year, today.month)
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "cal:ignore")


class WebhookConfigTests(unittest.TestCase):
    def test_default_mode_is_polling(self) -> None:
        s = config.build_webhook_settings({})
        self.assertEqual(s["bot_mode"], "polling")
        self.assertEqual(s["webhook_port"], config.DEFAULT_WEBHOOK_PORT)

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(SystemExit):
            config.build_webhook_settings({"BOT_MODE": "carrier-pigeon"})

    def test_webhook_mode_requires_url_and_secret(self) -> None:
        with self.assertRaises(SystemExit):
            config.build_webhook_settings({"BOT_MODE": "webhook"})
        with self.assertRaises(SystemExit):  # только URL, без секрета — тоже ошибка
            config.build_webhook_settings(
                {"BOT_MODE": "webhook", "WEBHOOK_URL": "https://x/y"}
            )

    def test_webhook_mode_full(self) -> None:
        s = config.build_webhook_settings({
            "BOT_MODE": "Webhook",           # регистр не важен
            "WEBHOOK_URL": " https://flights.example.com/hook ",  # обрезаем пробелы
            "WEBHOOK_SECRET": "s3cr3t",
            "WEBHOOK_PORT": "9000",
        })
        self.assertEqual(s["bot_mode"], "webhook")
        self.assertEqual(s["webhook_url"], "https://flights.example.com/hook")
        self.assertEqual(s["webhook_secret"], "s3cr3t")
        self.assertEqual(s["webhook_port"], 9000)

    def test_webhook_path_derived_from_url(self) -> None:
        self.assertEqual(
            bot_app._webhook_path("https://flights.example.com/abc123"), "abc123"
        )
        self.assertEqual(
            bot_app._webhook_path("https://flights.example.com/"), ""
        )


class MenuFormatTests(unittest.TestCase):
    def test_route_line_direct(self) -> None:
        line = menu._route_line({
            "origin": "MOW", "destination": "PEK", "depart_date": "2026-09-22",
            "direct_only": True, "stops_wanted": 0, "passengers": 1,
        })
        self.assertIn("прямой", line)
        self.assertNotIn("взросл", line)  # 1 пассажир не подписываем

    def test_route_line_with_stops_and_passengers(self) -> None:
        line = menu._route_line({
            "origin": "MOW", "destination": "IST", "depart_date": "2026-10-01",
            "direct_only": False, "stops_wanted": 1, "passengers": 2,
        })
        self.assertIn("ровно 1 пересадка", line)
        self.assertIn("2 взрослых", line)


class ChartTests(unittest.TestCase):
    def test_render_returns_png_bytes(self) -> None:
        series = [
            (_ROUTE, [
                {"ts": "2026-07-20 09:00:00", "price": 30100},
                {"ts": "2026-07-25 21:00:00", "price": 27829},
            ]),
        ]
        png = chart.render_price_chart(series)
        self.assertIsNotNone(png)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_render_empty_returns_none(self) -> None:
        self.assertIsNone(chart.render_price_chart([]))
        self.assertIsNone(chart.render_price_chart([(_ROUTE, [])]))


if __name__ == "__main__":
    unittest.main()
