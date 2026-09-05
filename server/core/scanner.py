"""
Асинхронный сканер медиа-директории.
Периодически проверяет директорию на наличие поддерживаемых видеофайлов
и передает результаты в менеджер очереди PlaylistManager.
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Set

from core.config import ConfigManager
from core.playlist import PlaylistManager

logger = logging.getLogger("stream_server.scanner")

# Поддерживаемые расширения медиафайлов
SUPPORTED_EXTENSIONS: Set[str] = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".ts",
}


class MediaScanner:
    """
    Периодический сканер папки с видеофайлами с возможностью принудительного триггера.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        playlist_manager: PlaylistManager,
    ):
        self.config_manager = config_manager
        self.playlist_manager = playlist_manager
        self._trigger_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._is_running = False

    def start(self) -> None:
        """Запуск фоновой задачи сканирования."""
        if self._task is None or self._task.done():
            self._is_running = True
            self._task = asyncio.create_task(self._scan_loop())
            logger.info("Асинхронный сканер директории запущен.")

    async def stop(self) -> None:
        """Остановка фоновой задачи сканирования."""
        self._is_running = False
        self._trigger_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Сканер директории остановлен.")

    def trigger_scan_now(self) -> None:
        """Принудительный запуск сканирования вне очереди таймера."""
        self._trigger_event.set()

    def _scan_directory_sync(self, directory: Path) -> List[Path]:
        """Синхронное чтение файлов из директории (выполняется в asyncio.to_thread)."""
        if not directory.exists():
            try:
                directory.mkdir(parents=True, exist_ok=True)
                logger.info(f"Создана директория для медиафайлов: {directory.resolve()}")
            except Exception as e:
                logger.error(f"Не удалось создать медиа-директорию {directory}: {e}")
                return []

        files = []
        try:
            for entry in directory.iterdir():
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(entry)
        except Exception as e:
            logger.error(f"Ошибка при сканировании директории {directory}: {e}")
            return []

        # Сортировка по имени для предсказуемого порядка
        files.sort(key=lambda p: p.name.lower())
        return files

    async def _scan_once(self) -> None:
        """Однократное сканирование и синхронизация с плейлистом."""
        settings = self.config_manager.get_settings()
        media_dir = Path(settings.media_dir)
        if not media_dir.is_absolute():
            base_server_dir = Path(__file__).resolve().parent.parent
            if (base_server_dir / media_dir).exists():
                media_dir = base_server_dir / media_dir

        scanned = await asyncio.to_thread(self._scan_directory_sync, media_dir)
        await self.playlist_manager.sync_with_scanned(scanned)

    async def _scan_loop(self) -> None:
        """Основной рабочий цикл сканера."""
        # Первичное сканирование сразу при старте
        await self._scan_once()

        while self._is_running:
            settings = self.config_manager.get_settings()
            interval = max(1, settings.scan_interval)

            try:
                # Ожидание либо истечения таймаута, либо внешнего события trigger_scan_now
                await asyncio.wait_for(self._trigger_event.wait(), timeout=interval)
                self._trigger_event.clear()
                logger.info("Выполняется принудительное сканирование по запросу...")
            except asyncio.TimeoutError:
                # Обычное периодическое срабатывание по таймеру
                pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Непредвиденная ошибка в цикле сканера: {e}")

            if not self._is_running:
                break

            await self._scan_once()
