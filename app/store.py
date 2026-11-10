from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Store:
    """SQLite index only. Message text is deliberately never stored here."""

    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS connections (
                    connection_id TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    owner_chat_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS message_index (
                    connection_id TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    owner_chat_id INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    original_header_id INTEGER NOT NULL,
                    latest_snapshot_ids TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(connection_id, chat_id, source_message_id)
                );
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id INTEGER PRIMARY KEY
                );
                """
            )

    def save_connection(self, connection_id: str, owner_id: int,
                        owner_chat_id: int, enabled: bool) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO connections(connection_id, owner_id, owner_chat_id, enabled)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(connection_id) DO UPDATE SET
                     owner_id=excluded.owner_id,
                     owner_chat_id=excluded.owner_chat_id,
                     enabled=excluded.enabled""",
                (connection_id, owner_id, owner_chat_id, int(enabled)),
            )

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM connections WHERE connection_id=?", (connection_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_message(self, *, connection_id: str, chat_id: int,
                     source_message_id: int, owner_id: int, owner_chat_id: int,
                     direction: str, sender: str, original_header_id: int,
                     latest_snapshot_ids: list[int], deleted: bool) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO message_index(
                     connection_id, chat_id, source_message_id, owner_id,
                     owner_chat_id, direction, sender, original_header_id,
                     latest_snapshot_ids, deleted
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (connection_id, chat_id, source_message_id, owner_id,
                 owner_chat_id, direction, sender, original_header_id,
                 json.dumps(latest_snapshot_ids), int(deleted)),
            )

    def get_message(self, connection_id: str, chat_id: int,
                    source_message_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM message_index
                   WHERE connection_id=? AND chat_id=? AND source_message_id=?""",
                (connection_id, chat_id, source_message_id),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["latest_snapshot_ids"] = json.loads(result["latest_snapshot_ids"])
            result["deleted"] = bool(result["deleted"])
            return result

    def update_latest_snapshot(self, connection_id: str, chat_id: int,
                               source_message_id: int, ids: list[int]) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE message_index SET latest_snapshot_ids=?
                   WHERE connection_id=? AND chat_id=? AND source_message_id=?""",
                (json.dumps(ids), connection_id, chat_id, source_message_id),
            )

    def mark_deleted(self, connection_id: str, chat_id: int,
                     source_message_id: int) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE message_index SET deleted=1
                   WHERE connection_id=? AND chat_id=? AND source_message_id=?""",
                (connection_id, chat_id, source_message_id),
            )

    def update_seen(self, update_id: int) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM processed_updates WHERE update_id=?", (update_id,)
            ).fetchone() is not None

    def mark_update_seen(self, update_id: int) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO processed_updates(update_id) VALUES(?)",
                (update_id,),
            )
