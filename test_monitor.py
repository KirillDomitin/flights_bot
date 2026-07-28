"""Мок-тесты логики сравнения цен (без реального API и Telegram).

Запуск:
    python -m unittest -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import monitor
import storage

_CONFIG = {
    "travelpayouts_token": "test-token",
    "telegram_bot_token": "test-bot",
    "telegram_chat_id": "123",
    # тесты мокают api_client.fetch_direct_price → используем источник "api"
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
            monitor.api_client, "fetch_direct_price", return_value=_record(price)
        ):
            return monitor.check_route(self.conn, _CONFIG, _ROUTE)

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
            monitor.api_client, "fetch_direct_price", return_value=None
        ):
            message = monitor.check_route(self.conn, _CONFIG, _ROUTE)
        self.assertIsNone(message)
        self.assertIsNone(
            storage.get_last_price(self.conn, "MOW", "PEK", "2025-09-22")
        )


class MessageFormatTests(unittest.TestCase):
    def test_diff_percent_in_message(self) -> None:
        import notifier

        msg = notifier.build_message(_record(38500), {"price": 42100})
        self.assertIn("38 500", msg)
        self.assertIn("было: 42 100", msg)
        self.assertIn("-9%", msg)


if __name__ == "__main__":
    unittest.main()
