"""SQLite 保管。縦持ち1テーブルにして、系列追加でスキーマを触らずに済ませる。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

# 既定はリポジトリ直下の build/。環境変数 KEIZAI_DATA_DIR で差し替えられる。
DATA_DIR = Path(os.environ.get("KEIZAI_DATA_DIR")
                or Path(__file__).resolve().parents[1] / "build")
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "market.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS series_daily (
    series_id TEXT NOT NULL,
    date      TEXT NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (series_id, date)
);
CREATE INDEX IF NOT EXISTS idx_series_daily_id_date ON series_daily(series_id, date);
CREATE TABLE IF NOT EXISTS fetch_log (
    series_id  TEXT NOT NULL,
    source     TEXT,
    fetched_at TEXT NOT NULL,
    rows       INTEGER,
    status     TEXT,
    message    TEXT
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, series_id: str, points: list[tuple[str, float]]) -> int:
    """(date, value) を投入する。既存日付は上書きする(確報で速報値を訂正するため)。"""
    rows = [(series_id, d, float(v)) for d, v in points if v is not None]
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO series_daily(series_id, date, value) VALUES (?,?,?) "
        "ON CONFLICT(series_id, date) DO UPDATE SET value=excluded.value",
        rows,
    )
    conn.commit()
    return len(rows)


def log_fetch(conn, series_id: str, source: str, rows: int, status: str, message: str = "") -> None:
    conn.execute(
        "INSERT INTO fetch_log(series_id, source, fetched_at, rows, status, message) VALUES (?,?,?,?,?,?)",
        (series_id, source, datetime.now().isoformat(timespec="seconds"), rows, status, message[:500]),
    )
    conn.commit()


def load(conn, series_id: str, since: str | None = None) -> list[tuple[str, float]]:
    sql = "SELECT date, value FROM series_daily WHERE series_id=?"
    args: list = [series_id]
    if since:
        sql += " AND date>=?"
        args.append(since)
    sql += " ORDER BY date"
    return [(r[0], r[1]) for r in conn.execute(sql, args)]


def load_all(conn, since: str | None = None) -> dict[str, list[tuple[str, float]]]:
    sql = "SELECT series_id, date, value FROM series_daily"
    args: list = []
    if since:
        sql += " WHERE date>=?"
        args.append(since)
    sql += " ORDER BY series_id, date"
    out: dict[str, list[tuple[str, float]]] = {}
    for sid, d, v in conn.execute(sql, args):
        out.setdefault(sid, []).append((d, v))
    return out


def coverage(conn) -> dict[str, tuple[str, str, int]]:
    """系列ごとの (最古日, 最新日, 件数)。"""
    sql = "SELECT series_id, MIN(date), MAX(date), COUNT(*) FROM series_daily GROUP BY series_id"
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(sql)}


def last_fetch(conn) -> dict[str, tuple[str, str, str]]:
    """系列ごとの直近取得 (時刻, 状態, メッセージ)。"""
    sql = ("SELECT series_id, fetched_at, status, COALESCE(message,'') FROM fetch_log f "
           "WHERE fetched_at = (SELECT MAX(fetched_at) FROM fetch_log WHERE series_id=f.series_id)")
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(sql)}


def is_empty(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM series_daily").fetchone()[0] == 0
