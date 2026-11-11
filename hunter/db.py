from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Listing

STATUSES = {
    "new": "חדש",
    "seen": "ראיתי",
    "interested": "מעניין",
    "contacted": "יצרתי קשר",
    "visit": "קבעתי צפייה",
    "visited": "ראיתי בפועל",
    "not_relevant": "לא רלוונטי",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    price INTEGER,
    price_history TEXT NOT NULL DEFAULT '[]',
    data TEXT NOT NULL,
    analysis TEXT,
    verdict TEXT,
    score REAL,
    details_fetched INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    notes TEXT NOT NULL DEFAULT '',
    status_updated TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT, finished TEXT, seen INTEGER, new INTEGER, notified INTEGER, error TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def get(self, key: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()

    def get_meta(self, k: str) -> Optional[str]:
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def set_meta(self, k: str, v: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v))
        self.commit()

    def upsert_seen(self, listing: Listing) -> tuple[bool, Optional[int]]:
        """Record that a listing appeared in the feed. Returns (is_new, old_price_if_changed)."""
        row = self.get(listing.key)
        ts = now()
        if row is None:
            hist = [[ts[:10], listing.price]] if listing.price else []
            self.conn.execute(
                "INSERT INTO listings (key, source, url, first_seen, last_seen, price, price_history, data) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (listing.key, listing.source, listing.url, ts, ts, listing.price,
                 json.dumps(hist), json.dumps(listing.to_dict(), ensure_ascii=False)),
            )
            return True, None
        old_price = row["price"]
        changed = listing.price and old_price and listing.price != old_price
        hist = json.loads(row["price_history"])
        if changed:
            hist.append([ts[:10], listing.price])
        self.conn.execute(
            "UPDATE listings SET last_seen=?, price=COALESCE(?, price), price_history=? WHERE key=?",
            (ts, listing.price, json.dumps(hist), listing.key),
        )
        return False, old_price if changed else None

    def save_analysis(self, listing: Listing, analysis: dict) -> None:
        self.conn.execute(
            "UPDATE listings SET data=?, analysis=?, verdict=?, score=?, details_fetched=?, price=COALESCE(?, price) "
            "WHERE key=?",
            (json.dumps(listing.to_dict(), ensure_ascii=False), json.dumps(analysis, ensure_ascii=False),
             analysis["verdict"], analysis["score"], int(listing.details_fetched), listing.price, listing.key),
        )

    def pending_details(self, limit: int) -> list[sqlite3.Row]:
        """Listings not yet analyzed in depth (and not already rejected from feed data)."""
        return self.conn.execute(
            "SELECT * FROM listings WHERE details_fetched=0 AND COALESCE(verdict,'') != 'reject' "
            "ORDER BY first_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def to_notify(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM listings WHERE notified=0 AND details_fetched=1 AND verdict IN ('match','check') "
            "ORDER BY score DESC"
        ).fetchall()

    def mark_notified(self, key: str, value: int = 1) -> None:
        self.conn.execute("UPDATE listings SET notified=? WHERE key=?", (value, key))

    def set_status(self, key: str, status: str, notes: Optional[str] = None) -> None:
        if notes is None:
            self.conn.execute("UPDATE listings SET status=?, status_updated=? WHERE key=?", (status, now(), key))
        else:
            self.conn.execute("UPDATE listings SET status=?, notes=?, status_updated=? WHERE key=?",
                              (status, notes, now(), key))
        self.commit()

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM listings ORDER BY score DESC NULLS LAST").fetchall()

    def log_run(self, started, seen, new, notified, error=None):
        self.conn.execute("INSERT INTO runs (started, finished, seen, new, notified, error) VALUES (?,?,?,?,?,?)",
                          (started, now(), seen, new, notified, error))
        self.commit()

    def last_runs(self, n=10):
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def commit(self):
        self.conn.commit()
