"""Асинхронный скрапер сайта расписания."""
import asyncio
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import unquote

import aiohttp
from bs4 import BeautifulSoup

from .link_finder import LinkFinder
from .atomic_file import AtomicFileReplace

logger = logging.getLogger(__name__)

BASE_URL = "https://aitanapa.ru/расписание-занятий/"
DOWNLOAD_DIR = "downloads"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
MIN_PDF_SIZE = 5 * 1024
HASH_CHUNK_SIZE = 65536


def _validate_pdf_sync(filepath: Path) -> tuple[bool, str | None]:
    """Синхронная проверка валидности PDF."""
    if not filepath.exists():
        return False, "Файл не существует"
    size = filepath.stat().st_size
    if size == 0:
        return False, "Файл пустой (0 байт)"
    if size < MIN_PDF_SIZE:
        return False, "Файл слишком маленький (минимум %s КБ)" % (MIN_PDF_SIZE // 1024)
    try:
        if filepath.read_bytes()[:4] != b"%PDF":
            return False, "Неверный заголовок PDF"
    except Exception as e:
        return False, "Ошибка чтения: %s" % e
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            if len(pdf.pages) == 0:
                return False, "PDF не содержит страниц"
    except ImportError:
        pass
    except Exception as e:
        return False, "Ошибка PDF: %s" % e
    return True, None


def _calculate_hash_sync(filepath: Path) -> str:
    """Синхронный расчёт SHA256 по чанкам."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


class ScheduleScraper:
    """Асинхронный скрапер расписания."""

    def __init__(self):
        self.download_path = Path(__file__).resolve().parent.parent / DOWNLOAD_DIR
        self.download_path.mkdir(exist_ok=True)
        self.link_finder = LinkFinder(BASE_URL)

    async def get_schedule_links(self) -> list[dict]:
        """Получает ссылки на расписание с сайта."""
        delay = __import__("random").uniform(1, 5)
        logger.info("Ожидание %.2f сек (jitter)...", delay)
        await asyncio.sleep(delay)

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(BASE_URL, timeout=timeout) as response:
                response.raise_for_status()
                html = await response.text()

        if "just a moment" in html.lower() or "cloudflare" in html.lower():
            logger.warning("Возможна защита от ботов (Cloudflare/WAF)")

        soup = BeautifulSoup(html, "html.parser")
        links = self.link_finder.find_all(soup)

        filtered = [
            link
            for link in links
            if "расписание" in link["text"].lower()
            or "raspis" in link["url"].lower()
        ]
        if filtered:
            logger.info("Найдено подходящих ссылок: %d", len(filtered))
            return filtered
        if links:
            logger.warning("Ссылки найдены, но фильтр не совпал")
            return links
        return []

    async def download_file(
        self, url: str, filename: str
    ) -> tuple[str | None, bool, str | None]:
        """Скачивает файл асинхронно. Возвращает (path, is_changed, hash)."""
        target = self.download_path / filename

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, timeout=timeout) as response:
                    response.raise_for_status()
                    cd = response.headers.get("Content-Disposition")
                    if cd:
                        m = re.search(
                            r"filename\*=UTF-8''(.+)|filename=\"?([^\";]+)\"?",
                            cd,
                        )
                        if m:
                            real_name = (m.group(1) or m.group(2) or "").strip()
                            if real_name:
                                filename = unquote(real_name)
                                target = self.download_path / filename

                    with AtomicFileReplace(target) as atomic:
                        total = 0
                        with open(atomic.temp, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                if chunk:
                                    f.write(chunk)
                                    total += len(chunk)
                        logger.info("Скачано %d байт", total)

                        loop = asyncio.get_running_loop()
                        is_valid, err = await loop.run_in_executor(
                            None, _validate_pdf_sync, atomic.temp
                        )
                        if not is_valid:
                            logger.error("Файл невалидный: %s", err)
                            return None, False, None

                        new_hash = await loop.run_in_executor(
                            None, _calculate_hash_sync, atomic.temp
                        )
                        old_hash = None
                        if target.exists():
                            old_hash = await loop.run_in_executor(
                                None, _calculate_hash_sync, target
                            )

                        if new_hash == old_hash:
                            logger.info("Файл не изменился (хеш совпадает)")
                            return str(target), False, new_hash
                        atomic.commit()
            logger.info("Файл сохранён: %s", target)
            return str(target), True, new_hash

        except asyncio.TimeoutError:
            logger.error("Таймаут при скачивании: %s", url)
            return None, False, None
        except aiohttp.ClientError as e:
            logger.error("Ошибка сети: %s", e)
            return None, False, None
        except OSError as e:
            logger.error("Ошибка ФС: %s", e)
            return None, False, None
        except Exception as e:
            logger.exception("Ошибка скачивания: %s", e)
            return None, False, None
