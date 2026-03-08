from __future__ import annotations

import aiosqlite


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def init(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                agreed_to_rules INTEGER NOT NULL DEFAULT 0,
                telegram_roll INTEGER,
                user_roll INTEGER,
                passed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.commit()

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, username, first_name),
        )
        await self.conn.commit()

    async def set_rules_agreement(self, user_id: int, agreed: bool) -> None:
        await self.conn.execute(
            """
            UPDATE users
            SET agreed_to_rules=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            """,
            (1 if agreed else 0, user_id),
        )
        await self.conn.commit()

    async def has_agreed(self, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT agreed_to_rules FROM users WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return bool(row["agreed_to_rules"]) if row else False

    async def save_telegram_roll(self, user_id: int, value: int) -> None:
        await self.conn.execute(
            """
            UPDATE users
            SET telegram_roll=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            """,
            (value, user_id),
        )
        await self.conn.commit()

    async def save_user_roll(self, user_id: int, value: int) -> None:
        await self.conn.execute(
            """
            UPDATE users
            SET user_roll=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            """,
            (value, user_id),
        )
        await self.conn.commit()

    async def set_passed(self, user_id: int, passed: bool) -> None:
        await self.conn.execute(
            """
            UPDATE users
            SET passed=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            """,
            (1 if passed else 0, user_id),
        )
        await self.conn.commit()
