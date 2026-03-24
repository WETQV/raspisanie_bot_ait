"""Experimental coordinate-based parser for timetable PDFs.

This script is intentionally separate from the production parser.
It probes a schedule PDF using fixed table coordinates and prints
the extracted schedule for one group in a readable form.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser.lesson_extractor import LessonExtractor
from tools.subject_alias_catalog import normalize_subject_alias


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


@dataclass
class Lesson:
    num: int
    time: str
    raw: str
    cleaned: str


EXTRACTOR = LessonExtractor()


def normalize_gap(prev_x1: float, next_x0: float, prev_text: str, next_text: str) -> str:
    gap = next_x0 - prev_x1
    if gap <= 0.8:
        return ""
    if prev_text.endswith((".", "-", "/")):
        return ""
    if next_text in {".", ",", ";", ":", ")", "/"}:
        return ""
    return " "


def join_words(words: list[dict]) -> str:
    if not words:
        return ""
    parts = [words[0]["text"]]
    prev = words[0]
    for word in words[1:]:
        parts.append(normalize_gap(prev["x1"], word["x0"], prev["text"], word["text"]))
        parts.append(word["text"])
        prev = word
    return "".join(parts).strip()


def split_word_lines(words: list[dict], tolerance: float = 2.5) -> list[list[dict]]:
    if not words:
        return []
    lines: list[list[dict]] = []
    current: list[dict] = [words[0]]
    current_top = words[0]["top"]
    for word in words[1:]:
        if abs(word["top"] - current_top) <= tolerance:
            current.append(word)
            continue
        lines.append(current)
        current = [word]
        current_top = word["top"]
    lines.append(current)
    return lines


def split_char_lines(chars: list[dict], tolerance: float = 2.0) -> list[list[dict]]:
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


def join_chars(chars: list[dict]) -> str:
    if not chars:
        return ""
    parts = [chars[0]["text"]]
    prev = chars[0]
    for char in chars[1:]:
        gap = char["x0"] - prev["x1"]
        if gap > 1.4 and prev["text"] not in {"(", "/", "-"} and char["text"] not in {".", ",", ";", ":", ")"}:
            parts.append(" ")
        parts.append(char["text"])
        prev = char
    return "".join(parts).strip()


def build_cell_text(cell_chars: list[dict]) -> str:
    lines = split_char_lines(sorted(cell_chars, key=lambda c: (round(c["top"], 1), c["x0"])))
    return "\n".join(join_chars(sorted(line, key=lambda c: c["x0"])) for line in lines if line).strip()


def extract_subject_text(raw_text: str) -> str:
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


def clean_cell_text(text: str) -> str:
    if "УП.07" in text:
        return "УП.07 Учебная практика"
    if "ПП.07" in text:
        return "ПП.07 Производственная практика"

    text = text.replace("\u00a0", " ")
    text = re.sub(r"(?<=[а-яё])(?=[А-ЯЁ])", " ", text)
    text = re.sub(r"(?<=[А-ЯЁ]\.) (?=[А-ЯЁ]\.)", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    text = text.replace("прак тика", "практика")
    text = text.replace("практика", "практика")
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

    # Common corruption in this PDF: group suffix leaks into teacher token.
    text = re.sub(r"\b\d-[А-Яа-яA-Za-z0-9]+еменский\b", "Кременский", text)

    text = re.sub(r"\s+", " ", text).strip(" -/")
    text = TEACHER_RE.sub("", text).strip()
    text = re.sub(r"(УП\.07 Учебная практика)(?:\s+\1)+", r"\1", text)
    text = re.sub(r"(ПП\.07 Произв\.практика)(?:\s+\1)+", r"\1", text)
    if text.islower() and len(text) <= 10:
        return ""
    if len(text) <= 2:
        return ""
    info = EXTRACTOR.extract(text)
    subject = (info.subject or text).strip()
    subject = LEADING_TEACHER_RE.sub("", subject).strip()
    subject = re.sub(r"\b(?:Т|Тж)\.?$", "", subject).strip()
    subject = re.sub(r"\bЗа\s+О\b", "", subject).strip()
    if subject.islower() and len(subject) <= 10:
        return ""
    if len(subject) <= 2:
        return ""
    return normalize_subject_alias(subject)


def find_target_region(page, group_name: str) -> tuple[float, float] | None:
    words = page.extract_words(
        x_tolerance=1,
        y_tolerance=1,
        use_text_flow=False,
        keep_blank_chars=False,
    )
    left_labels = sorted(
        [
            w
            for w in words
            if w["x0"] < 110
            and re.search(r"[А-ЯЁ]{2,}-\d-\d{2}", w.get("text", ""))
        ],
        key=lambda w: w["top"],
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


def collect_lessons(page, group_name: str) -> dict[str, list[Lesson]]:
    region = find_target_region(page, group_name)
    if region is None:
        return {}

    top, bottom = region
    words = page.extract_words(
        x_tolerance=1,
        y_tolerance=1,
        use_text_flow=False,
        keep_blank_chars=False,
    )
    chars = page.chars
    region_words = [w for w in words if top <= w["top"] < bottom]
    region_chars = [c for c in chars if top <= c["top"] < bottom]

    time_words = [
        w
        for w in region_words
        if 138 <= w["x0"] <= 161 and TIME_RE.match(w["text"])
    ]
    time_words.sort(key=lambda w: w["top"])

    result = {day: [] for day in DAY_NAMES}
    for index, time_word in enumerate(time_words):
        if index == 0:
            row_top = top
        else:
            row_top = (time_words[index - 1]["top"] + time_word["top"]) / 2
        if index + 1 < len(time_words):
            row_bottom = (time_word["top"] + time_words[index + 1]["top"]) / 2
        else:
            row_bottom = bottom

        time_value = time_word["text"].replace(".", ":")
        pair_candidates = [
            w
            for w in region_words
            if row_top <= w["top"] < row_bottom
            and 114 <= w["x0"] <= 121
            and w["text"].isdigit()
        ]
        if not pair_candidates:
            continue
        pair_word = min(pair_candidates, key=lambda w: abs(w["top"] - time_word["top"]))

        pair_num = int(pair_word["text"])
        time_value = PAIR_TIMES.get(pair_num, time_value)

        for day_name, (x0, x1) in zip(DAY_NAMES, DAY_X_RANGES):
            cell_chars = [
                c
                for c in region_chars
                if row_top <= c["top"] < row_bottom and x0 <= (c["x0"] + c["x1"]) / 2 < x1
            ]
            raw = build_cell_text(cell_chars)
            cleaned = clean_cell_text(extract_subject_text(raw))
            if cleaned:
                result[day_name].append(
                    Lesson(num=pair_num, time=time_value, raw=raw, cleaned=cleaned)
                )
    return result


def detect_period(pdf) -> str:
    first_page_text = pdf.pages[0].extract_text() or ""
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", first_page_text)
    return match.group(0) if match else "Не найдено"


def merge_lessons(target: dict[str, list[Lesson]], incoming: dict[str, list[Lesson]]) -> None:
    for day_name, lessons in incoming.items():
        seen = {(item.num, item.cleaned) for item in target[day_name]}
        for lesson in lessons:
            key = (lesson.num, lesson.cleaned)
            if key not in seen:
                target[day_name].append(lesson)
                seen.add(key)


def probe(pdf_path: Path, group_name: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        period = detect_period(pdf)
        merged = {day: [] for day in DAY_NAMES}
        hit_pages: list[int] = []
        for page_index, page in enumerate(pdf.pages, start=1):
            lessons = collect_lessons(page, group_name)
            if any(lessons.values()):
                merge_lessons(merged, lessons)
                hit_pages.append(page_index)
        if any(merged.values()):
            lines = [
                f"Группа: {group_name}",
                f"Период: {period}",
                f"Страницы: {', '.join(map(str, hit_pages))}",
                "",
            ]
            for day_name in DAY_NAMES:
                lines.append(day_name)
                day_lessons = sorted(merged[day_name], key=lambda lesson: lesson.num)
                if not day_lessons:
                    lines.append("  -")
                    continue
                for lesson in day_lessons:
                    lines.append(
                        f"  {lesson.num}. {lesson.time} | {lesson.cleaned}"
                    )
                    lines.append(f"     raw: {lesson.raw}")
                lines.append("")
            return "\n".join(lines).rstrip()
    return f"Группа {group_name} не найдена в PDF {pdf_path}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("group_name")
    args = parser.parse_args()
    print(probe(args.pdf_path, args.group_name))


if __name__ == "__main__":
    main()
