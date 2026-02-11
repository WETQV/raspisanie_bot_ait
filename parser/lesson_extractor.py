"""Извлечение кабинета и предмета из сырой строки PDF."""
from dataclasses import dataclass
import re
from typing import Optional

DOT_LIKE_CHARS = ("·", "∙", "•", "‧", "⋅", "・", "․")

ROOM_PATTERNS = [
    (r"([1-4])([^0-9\s\.]*)(\.)([^0-9\s\.]*)(\d{1,2})(\s*[БВбв]?)", "основной формат"),
    (r"([1-4])\s*[\.\/\-]\s*(\d{1,2})(\s*[БВбв]?)", "свободный формат"),
    (r"([а-яё]+)([1-4]\.\d{1,2})([БВ]?)(?:\s|$)", "прилипший к слову"),
    (r"([а-яё]+)([БВ])\s+([1-4]\.\d{1,2})(?:\s|$)", "буква перед кабинетом"),
    (r"([1-4])\s*\.\s*(\d{1,2})(\s*[БВбв]?)\s*$", "конец строки"),
]

SUBJECT_REPLACEMENTS = {
    r"Физическая культуртарен": "Физ-ра",
    r"Физическая культура": "Физ-ра",
    r"Ин\.язык в проф\.дея т\.": "Иностранный язык в проф.деятельности",
    r"Ин\.язык в проф\.дея.*": "Иностранный язык в проф.деятельности",
    r"Упр\.и автом\.баз да нных": "Управление и автоматизация баз данных",
    r"Упр\.и автом\.баз данных": "Управление и автоматизация баз данных",
    r"Упр\.и автом\.баз данны[Вв]х": "Управление и автоматизация баз данных",
    r"Сертиф\.инф\.систем.*": "Сертификация информационных систем",
}

SPECIAL_KEYWORDS = {
    "экзамен": "📝 ЭКЗАМЕН:",
    "консультация": "💡 КОНСУЛЬТАЦИЯ:",
    "дистант": "🌐 ДИСТАНТ:",
    "практика": "🛠 ПРАКТИКА:",
    "зачет": "✅ ЗАЧЕТ:",
}

FLOOR_RANGE = range(1, 5)
ROOM_NUM_RANGE = range(1, 16)


@dataclass
class LessonInfo:
    """Результат извлечения информации об уроке."""
    subject: str
    room: Optional[str]


class LessonExtractor:
    """Извлекает кабинет и предмет из сырой строки PDF."""

    def extract(self, raw_content: str) -> LessonInfo:
        """Извлекает предмет и кабинет из сырой строки."""
        if not raw_content:
            return LessonInfo(subject="", room=None)
        text = self._clean_text(raw_content)
        room, text = self._extract_room(text)
        text = self._remove_teacher(text)
        text = self._apply_special_keywords(text)
        text = self._apply_replacements(text)
        text = self._final_cleanup(text)
        return LessonInfo(subject=text, room=room)

    def _clean_text(self, text: str) -> str:
        text = text.strip()
        text = text.replace("\u00A0", " ")
        text = text.replace(",", ".")
        for ch in DOT_LIKE_CHARS:
            text = text.replace(ch, ".")
        text = re.sub(r"\.\.+", ".", text)
        return text

    def _extract_room(self, text: str) -> tuple[Optional[str], str]:
        for pattern, strategy in ROOM_PATTERNS:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for match in reversed(matches):
                result = self._validate_room_match(match, text, strategy)
                if result:
                    return result
        return None, text

    def _validate_room_match(
        self, match: re.Match, text: str, strategy: str
    ) -> Optional[tuple[str, str]]:
        groups = match.groups()
        if strategy == "основной формат":
            try:
                floor, num = int(groups[0]), int(groups[4])
                if floor not in FLOOR_RANGE or num not in ROOM_NUM_RANGE:
                    return None
                suffix = self._extract_suffix(groups[5] or "")
                room = f"{floor}.{num}{suffix}"
                start, end = match.span()
                frag1 = (groups[1] or "") + (groups[3] or "")
                cleaned = (text[:start] + frag1 + text[end:]).strip()
                return room, cleaned
            except (ValueError, IndexError):
                return None
        if strategy == "свободный формат":
            try:
                floor, num = int(groups[0]), int(groups[1])
                if floor not in FLOOR_RANGE or num not in ROOM_NUM_RANGE:
                    return None
                suffix = self._extract_suffix(groups[2] or "")
                room = f"{floor}.{num}{suffix}"
                start, end = match.span()
                cleaned = (text[:start] + text[end:]).strip()
                return room, cleaned
            except (ValueError, IndexError):
                return None
        if strategy == "прилипший к слову":
            try:
                word_before, room_part, suffix_raw = groups[0], groups[1], groups[2] or ""
                parts = room_part.split(".")
                if len(parts) != 2:
                    return None
                floor, num = int(parts[0]), int(parts[1])
                if floor not in FLOOR_RANGE or num not in ROOM_NUM_RANGE:
                    return None
                suffix = self._extract_suffix(suffix_raw)
                room = f"{floor}.{num}{suffix}"
                start, end = match.span()
                cleaned = (text[:start] + word_before + text[end:]).strip()
                return room, cleaned
            except (ValueError, IndexError):
                return None
        if strategy == "буква перед кабинетом":
            try:
                word_part, letter, room_part = groups[0], groups[1].upper(), groups[2]
                parts = room_part.split(".")
                if len(parts) != 2:
                    return None
                floor, num = int(parts[0]), int(parts[1])
                if floor not in FLOOR_RANGE or num not in ROOM_NUM_RANGE:
                    return None
                room = f"{floor}.{num}{letter}"
                start, end = match.span()
                cleaned = (text[:start] + word_part + text[end:]).strip()
                return room, cleaned
            except (ValueError, IndexError):
                return None
        if strategy == "конец строки":
            try:
                floor, num = int(groups[0]), int(groups[1])
                if floor not in FLOOR_RANGE or num not in ROOM_NUM_RANGE:
                    return None
                suffix = self._extract_suffix(groups[2] or "")
                room = f"{floor}.{num}{suffix}"
                start = match.start()
                cleaned = text[:start].strip()
                return room, cleaned
            except (ValueError, IndexError):
                return None
        return None

    @staticmethod
    def _extract_suffix(suffix_raw: str) -> str:
        cleaned = suffix_raw.strip().upper().replace(" ", "")
        if cleaned and cleaned[0] in ("Б", "В"):
            return cleaned[0]
        return ""

    @staticmethod
    def _remove_teacher(text: str) -> str:
        pattern = r"\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?)?\s*$"
        return re.sub(pattern, "", text).strip()

    def _apply_special_keywords(self, text: str) -> str:
        text_lower = text.lower()
        for keyword, prefix in SPECIAL_KEYWORDS.items():
            if keyword in text_lower and prefix not in text:
                cleaned = re.sub(
                    re.escape(keyword), "", text, flags=re.IGNORECASE
                ).strip()
                return ("%s %s" % (prefix, cleaned)).strip(": ")
        return text

    def _apply_replacements(self, text: str) -> str:
        for pattern, replacement in SUBJECT_REPLACEMENTS.items():
            if re.search(pattern, text, re.IGNORECASE):
                return replacement
        return text

    @staticmethod
    def _final_cleanup(text: str) -> str:
        text = re.sub(r"\.\.+", ".", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
