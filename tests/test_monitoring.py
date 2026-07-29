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
from flight_monitor.core import monitoring
from flight_monitor.repository import cache, storage

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
        # Отдельная временная БД на каждый тест
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.conn = storage.get_connection(db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _run(self, price: int) -> str | None:
        """Выполнить check_route с замоканным API, вернуть текст уведомления/None."""
        with mock.patch.object(
            monitoring.api_client, "fetch_price", return_value=_record(price)
        ):
            return monitoring.check_route(self.conn, _CONFIG, _ROUTE)

    def test_first_run_notifies_and_saves(self) -> None:
        message = self._run(42100)
        self.assertIsNotNone(message)  # первый запуск → уведомление
        last = storage.get_last_price(self.conn, "MOW", "PEK", "2025-09-22")
        self.assertEqual(last["price"], 42100)

    def test_price_drop_notifies(self) -> None:
        self._run(42100)                 # первичная запись
        message = self._run(38500)       # цена ниже
        self.assertIsNotNone(message)
        last = storage.get_last_price(self.conn, "MOW", "PEK", "2025-09-22")
        self.assertEqual(last["price"], 38500)

    def test_price_same_does_not_notify(self) -> None:
        self._run(42100)
        self.assertIsNone(self._run(42100))

    def test_price_increase_does_not_notify_but_saves(self) -> None:
        self._run(42100)
        message = self._run(45000)
        self.assertIsNone(message)
        last = storage.get_last_price(self.conn, "MOW", "PEK", "2025-09-22")
        self.assertEqual(last["price"], 45000)

    def test_api_none_skips_everything(self) -> None:
        with mock.patch.object(
            monitoring.api_client, "fetch_price", return_value=None
        ):
            message = monitoring.check_route(self.conn, _CONFIG, _ROUTE)
        self.assertIsNone(message)
        self.assertIsNone(
            storage.get_last_price(self.conn, "MOW", "PEK", "2025-09-22")
        )


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
    def test_price_key_includes_source_mode_and_route(self) -> None:
        # _ROUTE без direct_only → по умолчанию прямой (mode=d)
        self.assertEqual(
            cache.price_key("browser", _ROUTE),
            "price:browser:d:MOW:PEK:2025-09-22",
        )
        # с пересадками → mode=c, ключ отличается
        route_c = dict(_ROUTE, direct_only=False)
        self.assertEqual(
            cache.price_key("browser", route_c),
            "price:browser:c:MOW:PEK:2025-09-22",
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
        self.conn = storage.get_connection(db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_seed_fills_empty_then_idempotent(self) -> None:
        self.assertEqual(storage.get_active_routes(self.conn), [])
        storage.seed_routes(self.conn, config.DEFAULT_ROUTES)
        routes = storage.get_active_routes(self.conn)
        self.assertEqual(len(routes), len(config.DEFAULT_ROUTES))
        self.assertEqual(routes[0]["origin"], "MOW")
        self.assertTrue(routes[0]["direct_only"])
        # повторный сид ничего не добавляет
        storage.seed_routes(self.conn, config.DEFAULT_ROUTES)
        self.assertEqual(len(storage.get_active_routes(self.conn)), len(config.DEFAULT_ROUTES))

    def test_add_and_remove_route(self) -> None:
        rid = storage.add_route(self.conn, "LED", "AER", "2026-10-01", direct_only=False)
        self.assertIsNotNone(rid)
        routes = storage.get_active_routes(self.conn)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["destination"], "AER")
        self.assertFalse(routes[0]["direct_only"])
        # удаление → пропадает из активных
        self.assertTrue(storage.remove_route(self.conn, rid))
        self.assertEqual(storage.get_active_routes(self.conn), [])
        # повторное удаление уже неактивного → False
        self.assertFalse(storage.remove_route(self.conn, rid))

    def test_add_duplicate_reactivates(self) -> None:
        rid = storage.add_route(self.conn, "LED", "AER", "2026-10-01", direct_only=True)
        storage.remove_route(self.conn, rid)
        self.assertEqual(storage.get_active_routes(self.conn), [])
        # тот же маршрут снова → включается обратно, без дублей
        rid2 = storage.add_route(self.conn, "LED", "AER", "2026-10-01", direct_only=True)
        self.assertEqual(rid2, rid)
        self.assertEqual(len(storage.get_active_routes(self.conn)), 1)


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
