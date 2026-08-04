"""Хранение цен и маршрутов.

Абстракция `Repository` отделяет остальной код от конкретной БД (сейчас SQLite),
как `Cache` — от Redis (см. [[cache.py]]). `monitor`/`bot` знают только методы
репозитория, а не тип базы; за интерфейсом можно держать другой бэкенд.

Каждый метод открывает собственное короткоживущее соединение: соединение SQLite
нельзя переиспользовать между потоками, а операции идут и из event loop бота, и из
worker-потоков (`asyncio.to_thread`). Поэтому один экземпляр `SqliteRepository`
потокобезопасен — общего соединения между потоками нет.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Protocol

logger = logging.getLogger(__name__)

# По умолчанию БД лежит в корне репозитория (рядом с monitor.py). Модуль в
# flight_monitor/repository/, поэтому корень — на два уровня выше.
# Путь можно переопределить через MONITOR_DB_PATH (в Docker — том вне образа).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("MONITOR_DB_PATH") or _REPO_ROOT / "prices.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL DEFAULT (datetime('now')),
    origin       TEXT    NOT NULL,
    destination  TEXT    NOT NULL,
    depart_date  TEXT    NOT NULL,
    price        INTEGER NOT NULL,
    airline      TEXT,
    flight_number INTEGER,
    link         TEXT,
    currency     TEXT
);
CREATE INDEX IF NOT EXISTS idx_route
    ON prices (origin, destination, depart_date, ts);

CREATE TABLE IF NOT EXISTS routes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    origin       TEXT    NOT NULL,
    destination  TEXT    NOT NULL,
    depart_date  TEXT    NOT NULL,
    direct_only  INTEGER NOT NULL DEFAULT 1,
    stops_wanted INTEGER NOT NULL DEFAULT 0,
    passengers   INTEGER NOT NULL DEFAULT 1,
    added_by     TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (origin, destination, depart_date, direct_only)
);
"""

# Колонки, добавленные после первой версии схемы. Для уже существующих БД (прод)
# CREATE TABLE IF NOT EXISTS их не добавит — накатываем ALTER-ом при подключении.
_ROUTE_MIGRATIONS = {
    "stops_wanted": "ALTER TABLE routes ADD COLUMN stops_wanted INTEGER NOT NULL DEFAULT 0",
    "passengers": "ALTER TABLE routes ADD COLUMN passengers INTEGER NOT NULL DEFAULT 1",
}


def _migrate_routes(conn: sqlite3.Connection) -> None:
    """Догнать схему routes недостающими колонками (идемпотентно)."""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(routes)")}
    for column, ddl in _ROUTE_MIGRATIONS.items():
        if column not in have:
            conn.execute(ddl)


def _route_row_to_dict(row: sqlite3.Row) -> dict:
    """Строка таблицы routes → dict маршрута (direct_only как bool)."""
    return {
        "id": row["id"],
        "origin": row["origin"],
        "destination": row["destination"],
        "depart_date": row["depart_date"],
        "direct_only": bool(row["direct_only"]),
        "stops_wanted": row["stops_wanted"],
        "passengers": row["passengers"],
    }


class Repository(Protocol):
    """Интерфейс хранилища цен и маршрутов (без утечки соединения БД наружу)."""

    def get_last_price(self, origin: str, destination: str, depart_date: str) -> Optional[dict]: ...
    def save_price(self, record: dict) -> None: ...
    def get_history(self, origin: Optional[str] = None, destination: Optional[str] = None, limit: int = 50) -> list[dict]: ...
    def get_route_series(self, origin: str, destination: str, depart_date: str, limit: int = 200) -> list[dict]: ...
    def get_active_routes(self) -> list[dict]: ...
    def add_route(self, origin: str, destination: str, depart_date: str, direct_only: bool = True, stops_wanted: int = 0, passengers: int = 1, added_by: Optional[str] = None) -> Optional[int]: ...
    def remove_route(self, route_id: int) -> bool: ...
    def seed_routes(self, default_routes: list[dict]) -> None: ...


class SqliteRepository:
    """Реализация `Repository` на SQLite. Каждая операция — своё соединение."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        path = self._db_path
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_SCHEMA)
            _migrate_routes(conn)
            conn.commit()
            yield conn
        finally:
            conn.close()

    # --- Цены ---

    def get_last_price(self, origin: str, destination: str, depart_date: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM prices
                WHERE origin = ? AND destination = ? AND depart_date = ?
                ORDER BY ts DESC, id DESC LIMIT 1
                """,
                (origin, destination, depart_date),
            ).fetchone()
        return dict(row) if row else None

    def save_price(self, record: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prices
                    (origin, destination, depart_date, price,
                     airline, flight_number, link, currency)
                VALUES (:origin, :destination, :depart_date, :price,
                        :airline, :flight_number, :link, :currency)
                """,
                {
                    "origin": record["origin"],
                    "destination": record["destination"],
                    "depart_date": record["depart_date"],
                    "price": record["price"],
                    "airline": record.get("airline"),
                    "flight_number": record.get("flight_number"),
                    "link": record.get("link"),
                    "currency": record.get("currency"),
                },
            )
            conn.commit()
        logger.info(
            "Сохранена цена %s→%s %s: %s %s",
            record["origin"], record["destination"], record["depart_date"],
            record["price"], record.get("currency", ""),
        )

    def get_history(self, origin: Optional[str] = None, destination: Optional[str] = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM prices"
        params: list = []
        if origin and destination:
            query += " WHERE origin = ? AND destination = ?"
            params.extend([origin, destination])
        query += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_route_series(self, origin: str, destination: str, depart_date: str, limit: int = 200) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM prices
                WHERE origin = ? AND destination = ? AND depart_date = ?
                ORDER BY ts ASC, id ASC LIMIT ?
                """,
                (origin, destination, depart_date, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Маршруты ---

    def get_active_routes(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM routes WHERE active = 1 ORDER BY id ASC"
            ).fetchall()
        return [_route_row_to_dict(row) for row in rows]

    def add_route(self, origin: str, destination: str, depart_date: str, direct_only: bool = True, stops_wanted: int = 0, passengers: int = 1, added_by: Optional[str] = None) -> Optional[int]:
        # UNIQUE — по (origin, destination, depart_date, direct_only); повторное
        # добавление того же маршрута реактивирует его и обновляет число пересадок
        # и пассажиров (последний выбор пользователя выигрывает).
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO routes
                    (origin, destination, depart_date, direct_only, stops_wanted, passengers, added_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (origin, destination, depart_date, direct_only)
                DO UPDATE SET active = 1,
                              stops_wanted = excluded.stops_wanted,
                              passengers = excluded.passengers
                """,
                (origin, destination, depart_date, int(direct_only),
                 int(stops_wanted), int(passengers), added_by),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT id FROM routes
                WHERE origin = ? AND destination = ? AND depart_date = ? AND direct_only = ?
                """,
                (origin, destination, depart_date, int(direct_only)),
            ).fetchone()
        logger.info(
            "Добавлен маршрут %s→%s %s (%s, пассажиров: %d)",
            origin, destination, depart_date,
            "прямой" if direct_only else f"ровно {stops_wanted} пересадок",
            passengers,
        )
        return row["id"] if row else None

    def remove_route(self, route_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE routes SET active = 0 WHERE id = ? AND active = 1", (route_id,)
            )
            conn.commit()
            affected = cur.rowcount
        if affected:
            logger.info("Маршрут id=%s убран из мониторинга", route_id)
        return bool(affected)

    def seed_routes(self, default_routes: list[dict]) -> None:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM routes").fetchone()["n"]
        if count:
            return
        for route in default_routes:
            self.add_route(
                route["origin"], route["destination"], route["depart_date"],
                route.get("direct_only", True),
                route.get("stops_wanted", 0),
                route.get("passengers", 1),
            )
        logger.info("Таблица routes засеяна маршрутами по умолчанию (%d)", len(default_routes))


def build_repository(db_path: Optional[Path] = None) -> Repository:
    """Создать репозиторий. Сейчас только SQLite; за интерфейсом `Repository`
    можно добавить другой бэкенд (например, Postgres), не трогая вызывающий код."""
    return SqliteRepository(db_path or DB_PATH)
