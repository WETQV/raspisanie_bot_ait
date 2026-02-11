"""Атомарная замена файла с бэкапом и откатом при ошибке."""
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class AtomicFileReplace:
    """Контекстный менеджер для атомарной замены файла."""

    def __init__(self, target: Path):
        self.target = Path(target)
        self.temp = self.target.with_suffix(self.target.suffix + ".tmp")
        self.backup = self.target.with_suffix(self.target.suffix + ".backup")
        self._backed_up = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._rollback()
        self._cleanup()
        return False

    def write_temp(self, data: bytes) -> None:
        self.temp.write_bytes(data)

    def commit(self) -> None:
        if self.target.exists():
            shutil.copy2(self.target, self.backup)
            self._backed_up = True
            self.target.unlink()
        self.temp.rename(self.target)
        if self.backup.exists():
            self.backup.unlink()
            self._backed_up = False

    def _rollback(self) -> None:
        if self._backed_up and self.backup.exists() and not self.target.exists():
            shutil.copy2(self.backup, self.target)
            logger.info("Файл восстановлен из бэкапа")

    def _cleanup(self) -> None:
        for path in (self.temp, self.backup):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
