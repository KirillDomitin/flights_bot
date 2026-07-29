"""Хранение истории цен в SQLite."""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# По умолчанию БД лежит в корне репозитория (рядом с monitor.py). Модуль теперь
# в flight_monitor/repository/, поэтому корень — на два уровня выше.
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
    added_by     TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (origin, destination, depart_date, direct_only)
);
"""


def _route_row_to_dict(row: sqlite3.Row) -> dict:
    """Строка таблицы routes → dict маршрута (direct_only как bool)."""
    return {
        "id": row["id"],
        "origin": row["origin"],
        "destination": row["destination"],
        "depart_date": row["depart_date"],
        "direct_only": bool(row["direct_only"]),
    }


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Открыть соединение с БД и убедиться, что схема создана."""
    db_path = Path(db_path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


@contextmanager
def connect(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Контекстный менеджер: открыть соединение и гарантированно его закрыть.

    Соединение SQLite нужно создавать и использовать в одном потоке, поэтому
    в фоновых задачах оборачиваем весь блок работы с БД в этот менеджер.
    """
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_last_price(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    depart_date: str,
) -> Optional[dict]:
    """Вернуть последнюю сохранённую запись по маршруту или None."""
    row = conn.execute(
        """
        SELECT * FROM prices
        WHERE origin = ? AND destination = ? AND depart_date = ?
        ORDER BY ts DESC, id DESC
        LIMIT 1
        """,
        (origin, destination, depart_date),
    ).fetchone()
    return dict(row) if row else None


def save_price(conn: sqlite3.Connection, record: dict) -> None:
    """Сохранить запись о цене в БД."""
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
        record["origin"],
        record["destination"],
        record["depart_date"],
        record["price"],
        record.get("currency", ""),
    )


def get_history(
    conn: sqlite3.Connection,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Вернуть историю цен (опционально по конкретному маршруту)."""
    query = "SELECT * FROM prices"
    params: list = []
    if origin and destination:
        query += " WHERE origin = ? AND destination = ?"
        params.extend([origin, destination])
    query += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_route_series(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    depart_date: str,
    limit: int = 200,
) -> list[dict]:
    """Вернуть историю цен по конкретному маршруту в хронологическом порядке
    (по возрастанию времени) — для построения графика."""
    rows = conn.execute(
        """
        SELECT * FROM prices
        WHERE origin = ? AND destination = ? AND depart_date = ?
        ORDER BY ts ASC, id ASC
        LIMIT ?
        """,
        (origin, destination, depart_date, limit),
    ).fetchall()
    return [dict(row) for row in rows]


# --- Отслеживаемые маршруты (таблица routes) ---

def get_active_routes(conn: sqlite3.Connection) -> list[dict]:
    """Вернуть активные отслеживаемые маршруты (в порядке добавления)."""
    rows = conn.execute(
        "SELECT * FROM routes WHERE active = 1 ORDER BY id ASC"
    ).fetchall()
    return [_route_row_to_dict(row) for row in rows]


def add_route(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    depart_date: str,
    direct_only: bool = True,
    added_by: Optional[str] = None,
) -> Optional[int]:
    """Добавить маршрут в отслеживаемые. Вернуть id новой записи, либо None,
    если такой маршрут уже есть (UNIQUE). Если он был деактивирован — включает
    обратно."""
    cur = conn.execute(
        """
        INSERT INTO routes (origin, destination, depart_date, direct_only, added_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (origin, destination, depart_date, direct_only)
        DO UPDATE SET active = 1
        """,
        (origin, destination, depart_date, int(direct_only), added_by),
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
        "Добавлен маршрут %s→%s %s (%s)",
        origin, destination, depart_date,
        "прямой" if direct_only else "с пересадками",
    )
    return row["id"] if row else None


def remove_route(conn: sqlite3.Connection, route_id: int) -> bool:
    """Убрать маршрут из мониторинга (active = 0). История цен в prices остаётся.
    Вернуть True, если строка была затронута."""
    cur = conn.execute(
        "UPDATE routes SET active = 0 WHERE id = ? AND active = 1", (route_id,)
    )
    conn.commit()
    if cur.rowcount:
        logger.info("Маршрут id=%s убран из мониторинга", route_id)
    return bool(cur.rowcount)


def seed_routes(conn: sqlite3.Connection, default_routes: list[dict]) -> None:
    """Заполнить таблицу routes маршрутами по умолчанию, если она пуста
    (первый запуск). Идемпотентно: при непустой таблице ничего не делает."""
    count = conn.execute("SELECT COUNT(*) AS n FROM routes").fetchone()["n"]
    if count:
        return
    for route in default_routes:
        add_route(
            conn,
            route["origin"],
            route["destination"],
            route["depart_date"],
            route.get("direct_only", True),
        )
    logger.info("Таблица routes засеяна маршрутами по умолчанию (%d)", len(default_routes))
