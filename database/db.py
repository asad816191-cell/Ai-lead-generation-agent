"""SQLite persistence for searches and their leads."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from services.models import LEAD_KEYS

_UPDATABLE = {"status", "stage", "progress", "message", "warnings", "result_count", "finished_at",
              "industry", "location", "requested_count", "filters"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        lead_columns = ",\n".join(f"{k} TEXT" for k in LEAD_KEYS)
        with self.connect() as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT NOT NULL,
                    industry TEXT, location TEXT, requested_count INTEGER,
                    filters TEXT DEFAULT '[]',
                    enrich INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'queued',
                    stage TEXT DEFAULT '',
                    progress INTEGER DEFAULT 0,
                    message TEXT DEFAULT '',
                    warnings TEXT DEFAULT '[]',
                    result_count INTEGER DEFAULT 0,
                    created_at TEXT, finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                    position INTEGER,
                    {lead_columns}
                );
                CREATE INDEX IF NOT EXISTS idx_leads_search ON leads(search_id);
            """)

    # ---- searches ------------------------------------------------------------------
    def create_search(self, prompt: str, industry: str, location: str, count: int,
                      filters: list[str], enrich: bool) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO searches (prompt, industry, location, requested_count, filters, enrich, "
                "status, stage, message, created_at) VALUES (?,?,?,?,?,?, 'queued', 'queued', 'Queued', ?)",
                (prompt, industry, location, count, json.dumps(filters), int(enrich), _now()))
            return int(cur.lastrowid)

    def update_search(self, search_id: int, **fields) -> None:
        sets, values = [], []
        for key, value in fields.items():
            if key not in _UPDATABLE:
                raise ValueError(f"Cannot update field {key}")
            if key in ("warnings", "filters"):
                value = json.dumps(value)
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return
        with self.connect() as conn:
            conn.execute(f"UPDATE searches SET {', '.join(sets)} WHERE id = ?", (*values, search_id))

    @staticmethod
    def _search_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        for key in ("warnings", "filters"):
            try:
                data[key] = json.loads(data.get(key) or "[]")
            except ValueError:
                data[key] = []
        return data

    def get_search(self, search_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        return self._search_row(row) if row else None

    def list_searches(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM searches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._search_row(r) for r in rows]

    def delete_search(self, search_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
            return cur.rowcount > 0

    def mark_interrupted(self) -> None:
        """Searches still 'running' after a restart can never finish - flag them."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE searches SET status='failed', stage='interrupted', "
                "message='The app was restarted before this search finished.', finished_at=? "
                "WHERE status IN ('queued','running')", (_now(),))

    # ---- leads ---------------------------------------------------------------------
    def save_leads(self, search_id: int, leads: list[dict]) -> None:
        cols = ", ".join(["search_id", "position", *LEAD_KEYS])
        marks = ", ".join("?" * (len(LEAD_KEYS) + 2))
        with self.connect() as conn:
            conn.execute("DELETE FROM leads WHERE search_id = ?", (search_id,))
            conn.executemany(
                f"INSERT INTO leads ({cols}) VALUES ({marks})",
                [(search_id, i, *[str(lead.get(k, "Not Found")) for k in LEAD_KEYS])
                 for i, lead in enumerate(leads, start=1)])

    def get_leads(self, search_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(LEAD_KEYS)} FROM leads WHERE search_id = ? ORDER BY position",
                (search_id,)).fetchall()
        return [dict(r) for r in rows]
