"""SQLite-backed persistence for TRPG players and active slots."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from typing import Dict, Iterable, Tuple


class PlayerDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS players (
                    user_id TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1,
                    current_area TEXT NOT NULL DEFAULT 'area_00village',
                    prestige_count INTEGER NOT NULL DEFAULT 0,
                    language TEXT NOT NULL DEFAULT 'zh',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (user_id, slot)
                );

                CREATE TABLE IF NOT EXISTS active_slots (
                    user_id TEXT PRIMARY KEY,
                    slot TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._ensure_column(conn, "players", "level", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "players", "current_area", "TEXT NOT NULL DEFAULT 'area_00village'")
            self._ensure_column(conn, "players", "prestige_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "players", "language", "TEXT NOT NULL DEFAULT 'zh'")
            self._ensure_column(conn, "active_slots", "updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_players_level ON players(level)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_players_current_area ON players(current_area)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_players_prestige_count ON players(prestige_count)")
            conn.commit()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            if "CURRENT_TIMESTAMP" in definition:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
                conn.execute(f"UPDATE {table} SET {column} = CURRENT_TIMESTAMP WHERE {column} IS NULL")
            else:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def has_data(self) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT 1 FROM players LIMIT 1").fetchone()
            return row is not None

    def load(self) -> Tuple[Dict[str, dict], Dict[str, str]]:
        players = {}
        active_slots = {}
        with closing(self._connect()) as conn:
            for user_id, slot, data_json in conn.execute(
                "SELECT user_id, slot, data_json FROM players"
            ):
                payload = json.loads(data_json)
                players[f"{user_id}_{slot}"] = payload
            for user_id, slot in conn.execute("SELECT user_id, slot FROM active_slots"):
                active_slots[str(user_id)] = str(slot)
        return players, active_slots

    @staticmethod
    def _player_row(player: object) -> tuple:
        player_dict = player.to_dict()
        return (
            str(player.id),
            str(getattr(player, "character_slot", "0")),
            int(getattr(player, "level", 1) or 1),
            str(getattr(player, "current_area", "area_00village") or "area_00village"),
            int(getattr(player, "prestige_count", 0) or 0),
            str(getattr(player, "language", "zh") or "zh"),
            json.dumps(player_dict, ensure_ascii=False),
        )

    def sync_all(self, players: Dict[str, object], active_slots: Dict[str, str]):
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM players")
            conn.execute("DELETE FROM active_slots")
            conn.executemany(
                """
                INSERT INTO players (user_id, slot, level, current_area, prestige_count, language, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [self._player_row(player) for player in players.values()],
            )
            conn.executemany(
                "INSERT INTO active_slots (user_id, slot, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                [(str(uid), str(slot)) for uid, slot in active_slots.items()],
            )
            conn.commit()

    def upsert_players(self, players: Iterable[object]):
        rows = [self._player_row(player) for player in players]
        if not rows:
            return
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO players (user_id, slot, level, current_area, prestige_count, language, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, slot) DO UPDATE SET
                    level=excluded.level,
                    current_area=excluded.current_area,
                    prestige_count=excluded.prestige_count,
                    language=excluded.language,
                    data_json=excluded.data_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                rows,
            )
            conn.commit()

    def delete_players(self, player_keys: Iterable[str]):
        rows = []
        for key in player_keys:
            parts = str(key).split("_", 1)
            uid = parts[0]
            slot = parts[1] if len(parts) > 1 else "0"
            rows.append((uid, slot))
        if not rows:
            return
        with closing(self._connect()) as conn:
            conn.executemany("DELETE FROM players WHERE user_id = ? AND slot = ?", rows)
            conn.commit()

    def upsert_active_slots(self, active_slots: Dict[str, str]):
        rows = [(str(uid), str(slot)) for uid, slot in active_slots.items()]
        if not rows:
            return
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO active_slots (user_id, slot, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    slot=excluded.slot,
                    updated_at=CURRENT_TIMESTAMP
                """,
                rows,
            )
            conn.commit()

    def delete_active_slots(self, user_ids: Iterable[str]):
        rows = [(str(uid),) for uid in user_ids]
        if not rows:
            return
        with closing(self._connect()) as conn:
            conn.executemany("DELETE FROM active_slots WHERE user_id = ?", rows)
            conn.commit()
