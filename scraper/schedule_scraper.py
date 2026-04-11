"""Async scraper for the schedule website."""
import asyncio
import hashlib
import logging
import re
import secrets
from pathlib import Path
from urllib.parse import unquote, urljoin

import aiohttp
from bs4 import BeautifulSoup

from .atomic_file import AtomicFileReplace
from .link_finder import LinkFinder, is_allowed_schedule_url

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
MAX_PDF_SIZE = 50 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_REDIRECTS = 3
HASH_CHUNK_SIZE = 65536
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _validate_pdf_sync(filepath: Path) -> tuple[bool, str | None]:
    """Validate that the downloaded file looks like a real PDF."""
    if not filepath.exists():
        return False, "Файл не существует"

    size = filepath.stat().st_size
    if size == 0:
        return False, "Файл пустой (0 байт)"
    if size < MIN_PDF_SIZE:
        return False, f"Файл слишком маленький (минимум {MIN_PDF_SIZE // 1024} КБ)"
    if size > MAX_PDF_SIZE:
        return False, f"PDF exceeds maximum size ({MAX_PDF_SIZE // 1024 // 1024} MB)"

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
            if len(pdf.pages) > MAX_PDF_PAGES:
                return False, f"PDF has too many pages ({len(pdf.pages)} > {MAX_PDF_PAGES})"
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


def _require_allowed_url(url: str) -> str:
    if not is_allowed_schedule_url(url):
        raise ValueError("URL is outside the allowed schedule host allowlist")
    return url


def _content_length_too_large(response: aiohttp.ClientResponse) -> bool:
    header = response.headers.get("Content-Length")
    if not header:
        return False
    try:
        return int(header) > MAX_PDF_SIZE
    except ValueError:
        return False


class ScheduleScraper:
    """Async scraper for fetching and downloading schedule files."""

    def __init__(self):
        self.download_path = Path(__file__).resolve().parent.parent / DOWNLOAD_DIR
        self.download_path.mkdir(exist_ok=True)
        self.link_finder = LinkFinder(BASE_URL)

    async def get_schedule_links(self) -> list[dict]:
        delay = secrets.SystemRandom().uniform(1, 5)
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
            current_url = _require_allowed_url(url)
        except ValueError as exc:
            logger.error("Unsafe download request rejected: %s", exc)
            return None, False, None

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                redirects = 0
                while True:
                    async with session.get(
                        current_url,
                        timeout=timeout,
                        allow_redirects=False,
                    ) as response:
                        if response.status in REDIRECT_STATUSES:
                            location = response.headers.get("Location")
                            if not location:
                                logger.error("Redirect response without Location header")
                                return None, False, None
                            if redirects >= MAX_REDIRECTS:
                                logger.error("Too many redirects while downloading schedule")
                                return None, False, None
                            next_url = urljoin(str(response.url), location)
                            try:
                                current_url = _require_allowed_url(next_url)
                            except ValueError as exc:
                                logger.error("Unsafe redirect rejected: %s", exc)
                                return None, False, None
                            redirects += 1
                            continue

                        response.raise_for_status()

                        if _content_length_too_large(response):
                            logger.error("Remote PDF exceeds maximum size before download")
                            return None, False, None

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
                                        if total > MAX_PDF_SIZE:
                                            logger.error("Remote PDF exceeds maximum size during download")
                                            return None, False, None
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
