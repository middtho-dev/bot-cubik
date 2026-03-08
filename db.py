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
                rules_status TEXT NOT NULL DEFAULT 'pending',
                selected_mode TEXT,
                last_request TEXT,
                telegram_roll INTEGER,
                user_roll INTEGER,
                passed INTEGER NOT NULL DEFAULT 0,
                last_rules_message_id INTEGER,
                last_menu_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await self._migrate_users_table()
        await self.conn.commit()

    async def _migrate_users_table(self) -> None:
        cursor = await self.conn.execute("PRAGMA table_info(users)")
        columns = {row["name"] for row in await cursor.fetchall()}

        if "rules_status" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN rules_status TEXT NOT NULL DEFAULT 'pending'")
        if "last_rules_message_id" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN last_rules_message_id INTEGER")
        if "last_menu_message_id" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN last_menu_message_id INTEGER")
        if "selected_mode" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN selected_mode TEXT")
        if "last_request" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN last_request TEXT")

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

    async def get_rules_status(self, user_id: int) -> str:
        cursor = await self.conn.execute(
            "SELECT rules_status, agreed_to_rules FROM users WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return "pending"
        if row["rules_status"]:
            return row["rules_status"]
        return "agreed" if row["agreed_to_rules"] else "pending"

    async def set_rules_agreement(self, user_id: int, agreed: bool) -> None:
        current_status = await self.get_rules_status(user_id)
        if current_status == "agreed":
            return

        status = "agreed" if agreed else "declined"
        await self.conn.execute(
            """
            UPDATE users
            SET agreed_to_rules=?, rules_status=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            """,
            (1 if agreed else 0, status, user_id),
        )
        await self.conn.commit()

    async def has_agreed(self, user_id: int) -> bool:
        cursor = await self.conn.execute("SELECT agreed_to_rules FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return bool(row["agreed_to_rules"]) if row else False

    async def has_passed(self, user_id: int) -> bool:
        cursor = await self.conn.execute("SELECT passed FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return bool(row["passed"]) if row else False

    async def get_selected_mode(self, user_id: int) -> str | None:
        cursor = await self.conn.execute("SELECT selected_mode FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row["selected_mode"] if row else None

    async def set_selected_mode(self, user_id: int, mode: str) -> None:
        await self.conn.execute(
            "UPDATE users SET selected_mode=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (mode, user_id),
        )
        await self.conn.commit()


    async def get_last_request(self, user_id: int) -> str | None:
        cursor = await self.conn.execute("SELECT last_request FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row["last_request"] if row else None

    async def save_request(self, user_id: int, request_text: str) -> None:
        await self.conn.execute(
            "UPDATE users SET last_request=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (request_text, user_id),
        )
        await self.conn.commit()

    async def save_telegram_roll(self, user_id: int, value: int) -> None:
        await self.conn.execute(
            "UPDATE users SET telegram_roll=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (value, user_id),
        )
        await self.conn.commit()

    async def save_user_roll(self, user_id: int, value: int) -> None:
        await self.conn.execute(
            "UPDATE users SET user_roll=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (value, user_id),
        )
        await self.conn.commit()

    async def set_passed(self, user_id: int, passed: bool) -> None:
        await self.conn.execute(
            "UPDATE users SET passed=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (1 if passed else 0, user_id),
        )
        await self.conn.commit()

    async def set_last_rules_message_id(self, user_id: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE users SET last_rules_message_id=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (message_id, user_id),
        )
        await self.conn.commit()

    async def get_last_menu_message_id(self, user_id: int) -> int | None:
        cursor = await self.conn.execute("SELECT last_menu_message_id FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row["last_menu_message_id"] if row else None

    async def set_last_menu_message_id(self, user_id: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE users SET last_menu_message_id=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (message_id, user_id),
        )
        await self.conn.commit()
