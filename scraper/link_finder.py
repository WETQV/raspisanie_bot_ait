"""Поиск ссылок на файлы расписания в HTML."""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://aitanapa.ru/расписание-занятий/"


class LinkFinder:
    """Находит ссылки на файлы в HTML страницы."""

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url

    def find_all(self, soup: BeautifulSoup) -> list[dict]:
        """Собирает все кандидатные ссылки с дедупликацией."""
        candidates = []
        candidates.extend(self._find_wpdm_buttons(soup))
        candidates.extend(self._find_data_download(soup))
        candidates.extend(self._find_direct_links(soup))

        seen_urls = set()
        results = []
        for elem, url in candidates:
            normalized = self._normalize_url(url)
            if not normalized or normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            text = self._extract_title(elem, soup)
            file_id = self._extract_file_id(normalized)
            results.append(
                {
                    "filename": "schedule_%s.pdf" % file_id,
                    "url": normalized,
                    "text": text.replace("Скачать", "").strip(),
                }
            )
        return results

    def _find_wpdm_buttons(self, soup: BeautifulSoup) -> list[tuple]:
        results = []
        for btn in soup.find_all("a", class_="wpdm-download-link"):
            url = btn.get("data-downloadurl") or btn.get("href")
            if url:
                results.append((btn, url))
        return results

    def _find_data_download(self, soup: BeautifulSoup) -> list[tuple]:
        results = []
        for elem in soup.select("[data-downloadurl]"):
            url = elem.get("data-downloadurl")
            if url:
                results.append((elem, url))
        return results

    def _find_direct_links(self, soup: BeautifulSoup) -> list[tuple]:
        results = []
        exts = (".pdf", ".xlsx", ".xls", ".doc", ".docx")
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "wpdmdl=" in href or href.endswith(exts):
                results.append((a, a["href"]))
        return results

    def _normalize_url(self, url: str) -> str | None:
        if not url:
            return None
        return urljoin(self.base_url, url)

    def _extract_title(self, elem, soup: BeautifulSoup) -> str:
        container = elem.find_parent("div", class_="w3eden") or elem.find_parent(
            ["div", "li", "td"]
        )
        if container:
            title = container.find(["h3", "div"], class_="package-title")
            if title:
                return title.get_text(strip=True)
            return container.get_text(separator=" ", strip=True)
        return elem.get_text(strip=True) or "Unknown File"

    @staticmethod
    def _extract_file_id(url: str) -> str:
        m = re.search(r"wpdmdl=(\d+)", url)
        return m.group(1) if m else "unknown"
