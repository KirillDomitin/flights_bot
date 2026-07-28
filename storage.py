"""Хранение истории цен в SQLite."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).with_name("prices.db")

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
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Открыть соединение с БД и убедиться, что схема создана."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


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
