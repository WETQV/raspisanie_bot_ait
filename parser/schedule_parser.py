"""Парсер расписания из PDF."""
import pdfplumber
import re
from typing import Optional

from .lesson_extractor import LessonExtractor, LessonInfo

PAIR_TIMES = {
    1: "08:00-09:20",
    2: "09:30-10:50",
    3: "11:10-12:20",
    4: "12:40-14:00",
    5: "14:10-15:30",
    6: "15:40-17:00",
}
TIME_TO_NUM = {v: k for k, v in PAIR_TIMES.items()}

COLUMN_GROUP = 0
COLUMN_LESSON_NUM = 2
COLUMN_TIME = 3
COLUMN_DAYS_START = 4
DAY_COLUMNS = [
    (COLUMN_DAYS_START + i, day)
    for i, day in enumerate(
        [
            "ПОНЕДЕЛЬНИК",
            "ВТОРНИК",
            "СРЕДА",
            "ЧЕТВЕРГ",
            "ПЯТНИЦА",
            "СУББОТА",
        ]
    )
]


class ScheduleParser:
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.extractor = LessonExtractor()

    def _extract_lesson_info(
        self, raw_content: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Возвращает (subject, room) для обратной совместимости."""
        info = self.extractor.extract(raw_content)
        subject = info.subject or ""
        room = info.room
        if not subject.replace(".", "").strip() and not room:
            return None, None
        return subject, room

    def _normalize_time(
        self, raw_time: str
    ) -> tuple[Optional[int], Optional[str]]:
        if not raw_time:
            return None, None
        clean_time = raw_time.replace(".", ":").replace(" ", "").strip()
        clean_time = clean_time.replace("–", "-").replace("_", "-")
        time_match = re.search(
            r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", clean_time
        )
        if time_match:
            clean_time = "%s-%s" % (
                time_match.group(1),
                time_match.group(2),
            )
        pair_num = TIME_TO_NUM.get(clean_time)
        if pair_num is not None:
            return pair_num, clean_time
        return None, clean_time

    def _extract_raw_data(
        self, target_group: str
    ) -> tuple[dict, list[list[str]]]:
        """Извлекает метаданные и сырые строки из PDF."""
        metadata = {}
        raw_rows = []
        with pdfplumber.open(self.pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text()
            if first_page_text:
                date_match = re.search(
                    r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})",
                    first_page_text,
                )
                if date_match:
                    metadata["period"] = date_match.group(0)
            for page in pdf.pages:
                for table in page.extract_tables():
                    raw_rows.extend(
                        self._filter_group_rows(table, target_group)
                    )
        return metadata, raw_rows

    @staticmethod
    def _filter_group_rows(
        table: list, target_group: str
    ) -> list[list[str]]:
        """Фильтрует строки таблицы, относящиеся к целевой группе."""
        rows = []
        current_group = None
        target_lower = target_group.lower()
        for row in table:
            clean_row = [
                (cell.strip().replace("\n", " ") if cell else "")
                for cell in row
            ]
            if all(c == "" for c in clean_row):
                continue
            if clean_row[COLUMN_GROUP]:
                current_group = clean_row[COLUMN_GROUP]
            if (
                current_group
                and target_lower in current_group.lower()
            ):
                rows.append(clean_row)
        return rows

    def _resolve_pair_info(
        self, row: list[str]
    ) -> tuple[Optional[int], Optional[str]]:
        """Определяет номер пары и время из строки."""
        raw_time = (
            row[COLUMN_TIME]
            if len(row) > COLUMN_TIME
            else ""
        )
        pair_num, clean_time = self._normalize_time(raw_time)
        if pair_num is not None:
            return pair_num, clean_time
        raw_lesson_num = (
            row[COLUMN_LESSON_NUM]
            if len(row) > COLUMN_LESSON_NUM
            else ""
        )
        if raw_lesson_num.isdigit():
            n = int(raw_lesson_num)
            pair_num = (n + 1) // 2
            if not clean_time:
                clean_time = PAIR_TIMES.get(pair_num, "??:??")
            return pair_num, clean_time
        return None, None

    def _process_rows(
        self, raw_rows: list[list[str]]
    ) -> list[dict]:
        """Обрабатывает сырые строки в структурированное расписание."""
        days_schedule = {day: [] for _, day in DAY_COLUMNS}
        for row in raw_rows:
            pair_num, clean_time = self._resolve_pair_info(row)
            if not pair_num:
                continue
            for col_idx, day_name in DAY_COLUMNS:
                if col_idx >= len(row) or not row[col_idx]:
                    continue
                content = row[col_idx]
                subject, room = self._extract_lesson_info(content)
                if subject is None and room is None:
                    continue
                days_schedule[day_name].append(
                    {
                        "num": pair_num,
                        "time": clean_time or "",
                        "subject": subject or "",
                        "room": room,
                        "raw": content,
                    }
                )
        for day in days_schedule:
            days_schedule[day].sort(key=lambda x: x["num"])
        return [
            {"day": day_name, "lessons": days_schedule[day_name]}
            for _, day_name in DAY_COLUMNS
        ]

    def parse(self, target_group: str) -> Optional[dict]:
        """Парсит PDF и возвращает расписание для группы."""
        metadata, raw_rows = self._extract_raw_data(target_group)
        if not raw_rows:
            return None
        schedule = self._process_rows(raw_rows)
        return {"metadata": metadata, "schedule": schedule}
