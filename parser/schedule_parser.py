"""Coordinate-based timetable parser for the college PDF layout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pdfplumber

from .lesson_extractor import LessonExtractor
from .subject_alias_catalog import normalize_subject_alias

DAY_NAMES = [
    "ПОНЕДЕЛЬНИК",
    "ВТОРНИК",
    "СРЕДА",
    "ЧЕТВЕРГ",
    "ПЯТНИЦА",
    "СУББОТА",
]

DAY_X_RANGES = [
    (164.0, 223.9),
    (224.0, 284.1),
    (284.2, 344.1),
    (344.2, 404.1),
    (404.2, 464.3),
    (464.4, 532.2),
]

PAIR_TIMES = {
    1: "08:00-09:20",
    2: "09:30-10:50",
    3: "11:10-12:20",
    4: "12:40-14:00",
    5: "14:10-15:30",
    6: "15:40-17:00",
}

TEACHER_RE = re.compile(r"\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?)?\s*$")
LEADING_TEACHER_RE = re.compile(r"^[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?\s*")
TIME_RE = re.compile(r"^\d{2}\.\d{2}-\d{2}\.\d{2}$")
GROUP_RE = re.compile(r"[А-ЯЁ]{2,}-\d-\d{2}")


@dataclass
class Lesson:
    num: int
    time: str
    raw: str
    subject: str
    room: Optional[str]


class ScheduleParser:
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.extractor = LessonExtractor()

    @staticmethod
    def _normalize_gap(prev_x1: float, next_x0: float, prev_text: str, next_text: str) -> str:
        gap = next_x0 - prev_x1
        if gap <= 0.8:
            return ""
        if prev_text.endswith((".", "-", "/")):
            return ""
        if next_text in {".", ",", ";", ":", ")", "/"}:
            return ""
        return " "

    def _split_char_lines(self, chars: list[dict], tolerance: float = 2.0) -> list[list[dict]]:
        if not chars:
            return []
        lines: list[list[dict]] = []
        current: list[dict] = [chars[0]]
        current_top = chars[0]["top"]
        for char in chars[1:]:
            if abs(char["top"] - current_top) <= tolerance:
                current.append(char)
                continue
            lines.append(current)
            current = [char]
            current_top = char["top"]
        lines.append(current)
        return lines

    def _join_chars(self, chars: list[dict]) -> str:
        if not chars:
            return ""
        parts = [chars[0]["text"]]
        prev = chars[0]
        for char in chars[1:]:
            gap = char["x0"] - prev["x1"]
            if (
                gap > 1.4
                and prev["text"] not in {"(", "/", "-"}
                and char["text"] not in {".", ",", ";", ":", ")"}
            ):
                parts.append(" ")
            parts.append(char["text"])
            prev = char
        return "".join(parts).strip()

    def _build_cell_text(self, cell_chars: list[dict]) -> str:
        lines = self._split_char_lines(
            sorted(cell_chars, key=lambda c: (round(c["top"], 1), c["x0"]))
        )
        return "\n".join(
            self._join_chars(sorted(line, key=lambda c: c["x0"]))
            for line in lines
            if line
        ).strip()

    def _extract_subject_text(self, raw_text: str) -> str:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return ""
        first_line = lines[0]
        if len(lines) == 1:
            return first_line
        second_line = lines[1]
        if TEACHER_RE.search(" " + second_line):
            return first_line
        if re.search(r"[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.)", second_line):
            return first_line
        return f"{first_line} {second_line}".strip()

    def _clean_subject_text(self, text: str) -> str:
        if "УП.07" in text:
            return "УП.07 Учебная практика"
        if "ПП.07" in text:
            return "ПП.07 Производственная практика"

        text = text.replace("\u00a0", " ")
        text = re.sub(r"(?<=[а-яё])(?=[А-ЯЁ])", " ", text)
        text = re.sub(r"(?<=[А-ЯЁ]\.) (?=[А-ЯЁ]\.)", "", text)
        text = re.sub(r"\s+", " ", text).strip()

        text = text.replace("прак тика", "практика")
        text = text.replace("Произв.практ ика", "Произв.практика")
        text = text.replace("Учебная прак тика", "Учебная практика")
        text = text.replace("ухо ду", "уходу")
        text = re.sub(r"^[а-яё](?=[А-ЯЁ])", "", text)
        text = re.sub(r"^[а-яё]\s+", "", text)

        text = re.sub(r"\bУП\s+\d+-\d+\b", "", text)
        text = re.sub(r"\bПП\.07\s+\d+-\d+\b", "", text)
        text = re.sub(r"\b\d+-\d+\b", "", text)
        text = re.sub(r"\b(?:ЗаО|КЗаО|Защ\.?КП|пр\.)\b", "", text)
        text = re.sub(r"(?<=[А-Яа-яЁё])\d\.\d{1,2}[А-Яа-яЁёA-Za-z]*", "", text)
        text = re.sub(r"\b\d\.\d{1,2}[А-Яа-яЁёA-Za-z]*\b", "", text)
        text = re.sub(r"\b\d-[А-Яа-яA-Za-z0-9]+еменский\b", "Кременский", text)

        text = re.sub(r"\s+", " ", text).strip(" -/")
        text = TEACHER_RE.sub("", text).strip()
        text = re.sub(r"(УП\.07 Учебная практика)(?:\s+\1)+", r"\1", text)
        text = re.sub(r"(ПП\.07 Производственная практика)(?:\s+\1)+", r"\1", text)

        if text.islower() and len(text) <= 10:
            return ""
        if len(text) <= 2:
            return ""

        info = self.extractor.extract(text)
        subject = (info.subject or text).strip()
        subject = LEADING_TEACHER_RE.sub("", subject).strip()
        subject = re.sub(r"\b(?:Т|Тж)\.?$", "", subject).strip()
        subject = re.sub(r"\bЗа\s+О\b", "", subject).strip()

        if subject.islower() and len(subject) <= 10:
            return ""
        if len(subject) <= 2:
            return ""

        return normalize_subject_alias(subject)

    def _extract_room(self, raw_text: str) -> Optional[str]:
        info = self.extractor.extract(raw_text.replace("\n", " "))
        return info.room

    def _find_target_region(self, page, group_name: str) -> tuple[float, float] | None:
        words = page.extract_words(
            x_tolerance=1,
            y_tolerance=1,
            use_text_flow=False,
            keep_blank_chars=False,
        )
        left_labels = sorted(
            [
                word
                for word in words
                if word["x0"] < 110 and GROUP_RE.search(word.get("text", ""))
            ],
            key=lambda word: word["top"],
        )
        target_index = next(
            (
                index
                for index, word in enumerate(left_labels)
                if group_name.lower() in word.get("text", "").lower()
            ),
            None,
        )
        if target_index is None:
            return None

        target_hit = left_labels[target_index]
        if target_index > 0:
            prev_label = left_labels[target_index - 1]
            row_top = (prev_label["bottom"] + target_hit["top"]) / 2
        else:
            row_top = max(target_hit["top"] - 40, 0)

        if target_index + 1 < len(left_labels):
            next_label = left_labels[target_index + 1]
            row_bottom = (target_hit["bottom"] + next_label["top"]) / 2
        else:
            row_bottom = page.height
        return row_top, row_bottom

    def _collect_page_lessons(self, page, group_name: str) -> dict[str, list[Lesson]]:
        region = self._find_target_region(page, group_name)
        if region is None:
            return {}

        top, bottom = region
        words = page.extract_words(
            x_tolerance=1,
            y_tolerance=1,
            use_text_flow=False,
            keep_blank_chars=False,
        )
        region_words = [word for word in words if top <= word["top"] < bottom]
        region_chars = [char for char in page.chars if top <= char["top"] < bottom]

        time_words = [
            word
            for word in region_words
            if 138 <= word["x0"] <= 161 and TIME_RE.match(word["text"])
        ]
        time_words.sort(key=lambda word: word["top"])

        result = {day: [] for day in DAY_NAMES}
        for index, time_word in enumerate(time_words):
            row_top = top if index == 0 else (time_words[index - 1]["top"] + time_word["top"]) / 2
            row_bottom = (
                bottom
                if index + 1 == len(time_words)
                else (time_word["top"] + time_words[index + 1]["top"]) / 2
            )

            pair_candidates = [
                word
                for word in region_words
                if row_top <= word["top"] < row_bottom
                and 114 <= word["x0"] <= 121
                and word["text"].isdigit()
            ]
            if not pair_candidates:
                continue
            pair_word = min(
                pair_candidates,
                key=lambda word: abs(word["top"] - time_word["top"]),
            )
            pair_num = int(pair_word["text"])
            clean_time = PAIR_TIMES.get(pair_num, time_word["text"].replace(".", ":"))

            for day_name, (x0, x1) in zip(DAY_NAMES, DAY_X_RANGES):
                cell_chars = [
                    char
                    for char in region_chars
                    if row_top <= char["top"] < row_bottom
                    and x0 <= (char["x0"] + char["x1"]) / 2 < x1
                ]
                raw = self._build_cell_text(cell_chars)
                subject = self._clean_subject_text(self._extract_subject_text(raw))
                if not subject:
                    continue
                result[day_name].append(
                    Lesson(
                        num=pair_num,
                        time=clean_time,
                        raw=raw,
                        subject=subject,
                        room=self._extract_room(raw),
                    )
                )
        return result

    @staticmethod
    def _merge_lessons(target: dict[str, list[Lesson]], incoming: dict[str, list[Lesson]]) -> None:
        for day_name, lessons in incoming.items():
            seen = {
                (item.num, item.subject, item.room or "")
                for item in target[day_name]
            }
            for lesson in lessons:
                key = (lesson.num, lesson.subject, lesson.room or "")
                if key not in seen:
                    target[day_name].append(lesson)
                    seen.add(key)

    @staticmethod
    def _detect_period(pdf) -> str:
        first_page_text = pdf.pages[0].extract_text() or ""
        match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", first_page_text)
        return match.group(0) if match else "Не найдено"

    def parse(self, target_group: str) -> Optional[dict]:
        metadata: dict[str, str] = {}
        days_schedule = {day: [] for day in DAY_NAMES}

        with pdfplumber.open(self.pdf_path) as pdf:
            metadata["period"] = self._detect_period(pdf)
            hit = False
            for page in pdf.pages:
                lessons = self._collect_page_lessons(page, target_group)
                if any(lessons.values()):
                    hit = True
                    self._merge_lessons(days_schedule, lessons)

        if not hit:
            return None

        schedule = []
        for day_name in DAY_NAMES:
            lessons = sorted(days_schedule[day_name], key=lambda lesson: lesson.num)
            schedule.append(
                {
                    "day": day_name,
                    "lessons": [
                        {
                            "num": lesson.num,
                            "time": lesson.time,
                            "subject": lesson.subject,
                            "room": lesson.room,
                            "raw": lesson.raw,
                        }
                        for lesson in lessons
                    ],
                }
            )

        return {"metadata": metadata, "schedule": schedule}
