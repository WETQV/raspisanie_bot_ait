"""Async scraper for the schedule website."""
import asyncio
import hashlib
import logging
import random
import re
from pathlib import Path
from urllib.parse import unquote

import aiohttp
from bs4 import BeautifulSoup

from .atomic_file import AtomicFileReplace
from .link_finder import LinkFinder

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
    """Validate that the downloaded file looks like a real PDF."""
    if not filepath.exists():
        return False, "Файл не существует"

    size = filepath.stat().st_size
    if size == 0:
        return False, "Файл пустой (0 байт)"
    if size < MIN_PDF_SIZE:
        return False, f"Файл слишком маленький (минимум {MIN_PDF_SIZE // 1024} КБ)"

    try:
        if filepath.read_bytes()[:4] != b"%PDF":
            return False, "Неверный заголовок PDF"
    except Exception as exc:
        return False, f"Ошибка чтения: {exc}"

    try:
        import pdfplumber

        with pdfplumber.open(filepath) as pdf:
            if len(pdf.pages) == 0:
                return False, "PDF не содержит страниц"
    except ImportError:
        pass
    except Exception as exc:
        return False, f"Ошибка PDF: {exc}"

    return True, None


def _calculate_hash_sync(filepath: Path) -> str:
    """Calculate SHA256 in chunks."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as file_obj:
        while chunk := file_obj.read(HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sanitize_filename(filename: str) -> str:
    safe_name = Path(filename).name.strip()
    if not safe_name:
        raise ValueError("Empty filename")
    if Path(safe_name).suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are allowed")
    return safe_name


def _resolve_download_target(download_path: Path, filename: str) -> Path:
    safe_name = _sanitize_filename(filename)
    target = (download_path / safe_name).resolve()
    if target.parent != download_path.resolve():
        raise ValueError("Resolved file path escapes downloads directory")
    return target


class ScheduleScraper:
    """Async scraper for fetching and downloading schedule files."""

    def __init__(self):
        self.download_path = Path(__file__).resolve().parent.parent / DOWNLOAD_DIR
        self.download_path.mkdir(exist_ok=True)
        self.link_finder = LinkFinder(BASE_URL)

    async def get_schedule_links(self) -> list[dict]:
        delay = random.uniform(1, 5)
        logger.info("Waiting %.2f seconds before request", delay)
        await asyncio.sleep(delay)

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(BASE_URL, timeout=timeout) as response:
                response.raise_for_status()
                html = await response.text()

        if "just a moment" in html.lower() or "cloudflare" in html.lower():
            logger.warning("Possible anti-bot protection detected")

        soup = BeautifulSoup(html, "html.parser")
        links = self.link_finder.find_all(soup)

        filtered = [
            link
            for link in links
            if "расписание" in link["text"].lower() or "raspis" in link["url"].lower()
        ]
        if filtered:
            logger.info("Suitable links found: %d", len(filtered))
            return filtered
        if links:
            logger.warning("Links found, but no text matched the filter")
            return links
        return []

    async def download_file(
        self,
        url: str,
        filename: str,
    ) -> tuple[str | None, bool, str | None]:
        try:
            target = _resolve_download_target(self.download_path, filename)
        except ValueError as exc:
            logger.error("Unsafe generated filename rejected: %s", exc)
            return None, False, None

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, timeout=timeout) as response:
                    response.raise_for_status()

                    content_disposition = response.headers.get("Content-Disposition")
                    if content_disposition:
                        match = re.search(
                            r"filename\*=UTF-8''(.+)|filename=\"?([^\";]+)\"?",
                            content_disposition,
                        )
                        if match:
                            real_name = (match.group(1) or match.group(2) or "").strip()
                            if real_name:
                                try:
                                    target = _resolve_download_target(
                                        self.download_path,
                                        unquote(real_name),
                                    )
                                except ValueError as exc:
                                    logger.warning(
                                        "Ignoring unsafe Content-Disposition filename %r: %s",
                                        real_name,
                                        exc,
                                    )

                    with AtomicFileReplace(target) as atomic:
                        total = 0
                        with open(atomic.temp, "wb") as file_obj:
                            async for chunk in response.content.iter_chunked(8192):
                                if chunk:
                                    file_obj.write(chunk)
                                    total += len(chunk)
                        logger.info("Downloaded %d bytes", total)

                        loop = asyncio.get_running_loop()
                        is_valid, error = await loop.run_in_executor(
                            None,
                            _validate_pdf_sync,
                            atomic.temp,
                        )
                        if not is_valid:
                            logger.error("Downloaded file is invalid: %s", error)
                            return None, False, None

                        new_hash = await loop.run_in_executor(
                            None,
                            _calculate_hash_sync,
                            atomic.temp,
                        )
                        old_hash = None
                        if target.exists():
                            old_hash = await loop.run_in_executor(
                                None,
                                _calculate_hash_sync,
                                target,
                            )

                        if new_hash == old_hash:
                            logger.info("File hash unchanged")
                            return str(target), False, new_hash

                        atomic.commit()

            logger.info("File saved: %s", target)
            return str(target), True, new_hash
        except asyncio.TimeoutError:
            logger.error("Download timeout for %s", url)
            return None, False, None
        except aiohttp.ClientError as exc:
            logger.error("Network error: %s", exc)
            return None, False, None
        except OSError as exc:
            logger.error("Filesystem error: %s", exc)
            return None, False, None
        except Exception as exc:
            logger.exception("Unexpected download error: %s", exc)
            return None, False, None
