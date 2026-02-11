"""Database layer with a single connection and connection pooling."""
import aiosqlite
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DB_NAME = "bot_database.db"


class Database:
    """Обёртка над aiosqlite с единственным соединением."""

    def __init__(self, db_path: str = DB_NAME):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Установить соединение с БД."""
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._init_tables()
        logger.info("БД подключена: %s", self.db_path)

    async def close(self) -> None:
        """Закрыть соединение."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        return self._connection

    async def _init_tables(self) -> None:
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                chat_title TEXT,
                chat_type TEXT,
                message_thread_id INTEGER,
                group_name TEXT NOT NULL DEFAULT 'ИСП-3-22',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL,
                week_period TEXT NOT NULL,
                day_name TEXT NOT NULL,
                lesson_num INTEGER NOT NULL,
                time_start TEXT,
                time_end TEXT,
                subject TEXT NOT NULL,
                room TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_schedule_lookup
                ON schedule(group_name, week_period, day_name);

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # Миграция: добавляем message_thread_id если колонки нет
        async with self.conn.execute("PRAGMA table_info(chats)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if "message_thread_id" not in columns:
                logger.info("Миграция: добавляю колонку message_thread_id...")
                await self.conn.execute(
                    "ALTER TABLE chats ADD COLUMN message_thread_id INTEGER"
                )
        await self.conn.commit()

    # --- Методы для работы с чатами ---

    async def add_chat(
        self,
        chat_id: int,
        chat_title: str,
        chat_type: str,
        message_thread_id: Optional[int] = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO chats (chat_id, chat_title, chat_type, message_thread_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                message_thread_id = excluded.message_thread_id,
                chat_title = excluded.chat_title
            """,
            (chat_id, chat_title, chat_type, message_thread_id),
        )
        await self.conn.commit()

    async def get_chats(self) -> list[tuple[int, Optional[int]]]:
        async with self.conn.execute(
            "SELECT chat_id, message_thread_id FROM chats"
        ) as cursor:
            rows = await cursor.fetchall()
            return [(row[0], row[1]) for row in rows]

    async def is_chat_registered(self, chat_id: int) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def remove_chat(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    # --- Методы для расписания ---

    async def save_schedule(
        self, group_name: str, week_period: str, schedule_data: list[dict]
    ) -> None:
        await self.conn.execute(
            "DELETE FROM schedule WHERE group_name = ? AND week_period = ?",
            (group_name, week_period),
        )
        rows = []
        for day in schedule_data:
            day_name = day["day"]
            for lesson in day["lessons"]:
                parts = lesson["time"].split("-")
                t_start = parts[0].strip() if parts else ""
                t_end = parts[1].strip() if len(parts) > 1 else ""
                rows.append(
                    (
                        group_name,
                        week_period,
                        day_name,
                        lesson["num"],
                        t_start,
                        t_end,
                        lesson["subject"],
                        lesson.get("room"),
                    )
                )
        await self.conn.executemany(
            """
            INSERT INTO schedule
                (group_name, week_period, day_name, lesson_num,
                 time_start, time_end, subject, room)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await self.conn.commit()

    async def get_schedule_for_day(
        self,
        group_name: str,
        day_name: str,
        week_period: Optional[str] = None,
    ) -> tuple[Optional[str], list]:
        if not week_period:
            async with self.conn.execute(
                """SELECT week_period FROM schedule
                   WHERE group_name = ?
                   ORDER BY id DESC LIMIT 1""",
                (group_name,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None, []
                week_period = row[0]

            # Проверка актуальности периода
            try:
                end_date_str = week_period.split("-")[1].strip()
                end_date = datetime.strptime(end_date_str, "%d.%m.%Y")
                if end_date.date() < datetime.now().date() - timedelta(days=1):
                    return None, []
            except Exception:
                pass

        async with self.conn.execute(
            """
            SELECT lesson_num, time_start, time_end, subject, room
            FROM schedule
            WHERE group_name = ? AND day_name = ? AND week_period = ?
            ORDER BY lesson_num
            """,
            (group_name, day_name, week_period),
        ) as cursor:
            rows = await cursor.fetchall()
            # Return as list of tuples for backward compatibility
            result = [
                (row[0], row[1], row[2], row[3], row[4] or "")
                for row in rows
            ]
            return week_period, result

    # --- Метаданные ---

    async def get_metadata(self, key: str) -> Optional[str]:
        async with self.conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_metadata(self, key: str, value: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await self.conn.commit()


# Глобальный экземпляр (единственный допустимый глобал)
db = Database()


# --- Обратная совместимость: модульные функции делегируют в db ---

async def init_db() -> None:
    await db.connect()


async def add_chat(
    chat_id: int,
    chat_title: str,
    chat_type: str,
    message_thread_id: Optional[int] = None,
) -> None:
    await db.add_chat(chat_id, chat_title, chat_type, message_thread_id)


async def get_chats() -> list[tuple[int, Optional[int]]]:
    return await db.get_chats()


async def is_chat_registered(chat_id: int) -> bool:
    return await db.is_chat_registered(chat_id)


async def save_schedule(
    group_name: str, week_period: str, schedule_data: list[dict]
) -> None:
    await db.save_schedule(group_name, week_period, schedule_data)


async def get_schedule_for_day(
    group_name: str,
    day_name: str,
    week_period: Optional[str] = None,
) -> tuple[Optional[str], list]:
    return await db.get_schedule_for_day(group_name, day_name, week_period)


async def get_metadata(key: str) -> Optional[str]:
    return await db.get_metadata(key)


async def set_metadata(key: str, value: str) -> None:
    await db.set_metadata(key, value)


if __name__ == "__main__":
    asyncio.run(init_db())
