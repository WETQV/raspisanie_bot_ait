"""Data models for schedule and lessons."""
from dataclasses import dataclass
from typing import Any


@dataclass
class Lesson:
    """Один урок (пара) в расписании."""
    num: int
    time_start: str
    time_end: str
    subject: str
    room: str

    @classmethod
    def from_db_row(cls, row: tuple) -> "Lesson":
        """Создать из строки БД (lesson_num, time_start, time_end, subject, room)."""
        return cls(
            num=row[0],
            time_start=row[1] or "",
            time_end=row[2] or "",
            subject=row[3] or "",
            room=row[4] if len(row) > 4 and row[4] else "",
        )

    @classmethod
    def from_parser_dict(cls, d: dict) -> "Lesson":
        """Создать из словаря парсера (num, time, subject, room)."""
        parts = (d.get("time") or "").split("-")
        time_start = parts[0].strip() if parts else ""
        time_end = parts[1].strip() if len(parts) > 1 else ""
        return cls(
            num=d.get("num", 0),
            time_start=time_start,
            time_end=time_end,
            subject=d.get("subject", ""),
            room=d.get("room") or "",
        )


@dataclass
class DaySchedule:
    """Расписание на один день."""
    day_name: str
    lessons: list[Lesson]
