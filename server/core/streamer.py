"""
FFmpeg-оркестратор непрерывного вещания с предохранителем сбоев (Circuit Breaker).
Управляет процессами трансляции, обработкой ошибок и интеграцией оверлеев.
"""

import asyncio
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import ConfigManager
from core.playlist import PlaylistManager
from core.plugins.manager import PluginManager

logger = logging.getLogger("stream_server.streamer")


class StreamStatus(str, Enum):
    IDLE = "IDLE"  # Ожидание медиафайлов
    LIVE = "LIVE"  # Активное вещание
    CRITICAL_ERROR = "CRITICAL_ERROR"  # Сработал предохранитель (10+ ошибок подряд)


class StreamOrchestrator:
    """
    Оркестратор вещания: запускает FFmpeg для каждого файла,
    контролирует жизненный цикл процесса и состояние предохранителя.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        playlist_manager: PlaylistManager,
        plugin_manager: PluginManager,
    ):
        self.config_manager = config_manager
        self.playlist_manager = playlist_manager
        self.plugin_manager = plugin_manager

        self.status: StreamStatus = StreamStatus.IDLE
        self.consecutive_errors: int = 0
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.current_file: Optional[Path] = None
        self._black_process: Optional[asyncio.subprocess.Process] = None
        self._black_image_path: Path = Path(__file__).resolve().parent.parent / "config" / "black.png"

        self._skip_requested: bool = False
        self._manual_switch_requested: bool = False
        self._is_running: bool = False
        self._main_task: Optional[asyncio.Task] = None
        self._resume_event = asyncio.Event()

    def start(self) -> None:
        """Запуск главного цикла вещания."""
        if self._main_task is None or self._main_task.done():
            self._is_running = True
            self._main_task = asyncio.create_task(self._streaming_loop())
            logger.info("Оркестратор вещания успешно запущен.")

    async def stop(self, keep_black_alive: bool = False) -> None:
        """Полная остановка вещания (или перевод в режим вещания чёрного экрана)."""
        self._is_running = False
        self._resume_event.set()
        await self._terminate_current_process()
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
            self._main_task = None
        self.status = StreamStatus.IDLE
        if keep_black_alive:
            await self._start_black_stream()
            logger.info("Оркестратор вещания остановлен (активна трансляция чёрного экрана).")
        else:
            await self._stop_black_stream()
            logger.info("Оркестратор вещания остановлен.")

    async def skip_track(self) -> bool:
        """Принудительный пропуск текущего трека."""
        logger.info("Запрошен ручной пропуск текущего трека.")
        self._skip_requested = True
        if self.current_process and self.current_process.returncode is None:
            await self._terminate_current_process()
            return True
        else:
            await self.playlist_manager.skip()
            return True

    async def play_track(self, filename: str) -> bool:
        """Принудительный немедленный запуск указанного трека."""
        logger.info(f"Запрошен принудительный запуск трека: {filename}")
        item = await self.playlist_manager.set_current_by_name(filename)
        if not item:
            return False

        self._manual_switch_requested = True
        self._skip_requested = False
        if self.current_process and self.current_process.returncode is None:
            await self._terminate_current_process()
        elif self.status == StreamStatus.IDLE:
            self._resume_event.set()
        return True

    async def previous_track(self) -> bool:
        """Переход к предыдущему треку очереди."""
        logger.info("Запрошен переход к предыдущему треку.")
        item = await self.playlist_manager.previous()
        if not item:
            return False

        self._manual_switch_requested = True
        self._skip_requested = False
        if self.current_process and self.current_process.returncode is None:
            await self._terminate_current_process()
        elif self.status == StreamStatus.IDLE:
            self._resume_event.set()
        return True

    async def reload_pipeline(self) -> None:
        """
        Перезапуск текущего процесса FFmpeg для немедленного применения изменений в фильтрах/плагинах.
        Не переключает трек вперед (сохраняет текущий файл), а перезапускает его с новым -filter_complex.
        """
        logger.info("Запрошено обновление конвейера плагинов: перезапуск текущего трека с новыми фильтрами...")
        self._manual_switch_requested = True
        self._skip_requested = False
        if self.current_process and self.current_process.returncode is None:
            await self._terminate_current_process()
        elif self.status == StreamStatus.IDLE:
            self._resume_event.set()

    async def delete_media_file(self, filename: str) -> bool:
        """
        Безопасное удаление файла из папки media.
        Если файл в данный момент транслируется через FFmpeg,
        предварительно останавливает трансляцию/переключает трек,
        чтобы Windows освободила блокировку дескриптора файла.
        """
        settings = self.config_manager.get_settings()
        base_dir = Path(__file__).resolve().parent.parent
        configured_dir = Path(settings.media_dir)
        if configured_dir.is_absolute():
            media_dir = configured_dir.resolve()
        else:
            media_dir = (base_dir / configured_dir).resolve()

        # Защита от Path Traversal
        safe_filename = Path(filename).name
        if not safe_filename or safe_filename != filename:
            logger.warning(f"Попытка небезопасного удаления файла (Path Traversal): {filename}")
            raise ValueError("Некорректное имя файла")

        target_path = (media_dir / safe_filename).resolve()
        if not target_path.is_relative_to(media_dir):
            logger.warning(f"Путь файла {target_path} выходит за пределы {media_dir}")
            raise ValueError("Доступ запрещен: выход за пределы директории медиа")

        if not target_path.exists():
            logger.warning(f"Файл для удаления не найден на диске: {target_path}")
            await self.playlist_manager.remove_file(safe_filename)
            return False

        # Если файл сейчас воспроизводится в FFmpeg, освобождаем его дескриптор
        if self.current_file and self.current_file.resolve() == target_path:
            logger.info(f"Удаляемый файл {safe_filename} сейчас воспроизводится. Переключение трека...")
            playlist_state = await self.playlist_manager.get_state()
            if playlist_state["total"] > 1:
                await self.playlist_manager.advance()
                self._manual_switch_requested = True
            else:
                self.current_file = None
                self.status = StreamStatus.IDLE

            await self._terminate_current_process()
            # Ожидание освобождения дескриптора файловой системой Windows
            await asyncio.sleep(0.2)

        # Удаляем из очереди плейлиста
        await self.playlist_manager.remove_file(safe_filename)

        # Удаляем физический файл с диска
        try:
            target_path.unlink()
            logger.info(f"Файл {safe_filename} успешно удален с диска: {target_path}")
            return True
        except Exception as e:
            logger.error(f"Не удалось удалить файл {safe_filename} с диска: {e}")
            raise e

    async def rename_media_file(self, old_filename: str, new_filename: str) -> Path:
        """
        Переименование медиафайла на диске и в очереди воспроизведения.
        Корректно обрабатывает блокировки Windows при активной трансляции.
        """
        settings = self.config_manager.get_settings()
        base_dir = Path(__file__).resolve().parent.parent
        configured_dir = Path(settings.media_dir)
        if configured_dir.is_absolute():
            media_dir = configured_dir.resolve()
        else:
            media_dir = (base_dir / configured_dir).resolve()

        # Валидация имен файлов (защита от Path Traversal)
        safe_old = Path(old_filename).name
        safe_new = Path(new_filename).name
        if not safe_old or safe_old != old_filename:
            raise ValueError("Некорректное исходное имя файла")
        if not safe_new or safe_new != new_filename:
            raise ValueError("Некорректное новое имя файла")

        # Если пользователь не указал расширение, сохраняем исходное
        old_ext = Path(safe_old).suffix
        new_ext = Path(safe_new).suffix
        if not new_ext:
            safe_new = f"{safe_new}{old_ext}"

        old_path = (media_dir / safe_old).resolve()
        new_path = (media_dir / safe_new).resolve()

        if not old_path.is_relative_to(media_dir) or not new_path.is_relative_to(media_dir):
            raise ValueError("Доступ запрещен: выход за пределы директории медиа")

        if not old_path.exists():
            raise FileNotFoundError(f"Исходный файл {safe_old} не найден на сервере")

        if new_path.exists() and old_path != new_path:
            raise FileExistsError(f"Файл с именем {safe_new} уже существует")

        if old_path == new_path:
            return new_path

        # Если файл сейчас транслируется через FFmpeg
        is_current = bool(self.current_file and self.current_file.resolve() == old_path)
        if is_current:
            logger.info(f"Переименовываемый файл {safe_old} сейчас транслируется. Остановка FFmpeg...")
            await self._terminate_current_process()
            await asyncio.sleep(0.2)

        try:
            old_path.rename(new_path)
            logger.info(f"Файл успешно переименован на диске: {safe_old} -> {safe_new}")
        except PermissionError:
            # На Windows может потребоваться дополнительное время на закрытие дескриптора
            await asyncio.sleep(0.5)
            old_path.rename(new_path)

        # Обновляем имя в плейлисте
        await self.playlist_manager.rename_file(safe_old, safe_new)

        if is_current:
            self.current_file = new_path
            self._manual_switch_requested = True
            if self.status == StreamStatus.IDLE:
                self._resume_event.set()

        return new_path

    async def reset_circuit_breaker(self) -> None:
        """Сброс счетчика аварий и возобновление трансляции после блокировки."""
        logger.info("Выполняется ручной сброс предохранителя (Circuit Breaker).")
        self.consecutive_errors = 0
        if self.status == StreamStatus.CRITICAL_ERROR:
            self.status = StreamStatus.IDLE
            self._resume_event.set()

    async def _terminate_current_process(self) -> None:
        """Безопасное завершение текущего процесса FFmpeg."""
        proc = self.current_process
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except Exception as e:
                logger.debug(f"Исключение при остановке FFmpeg процесса: {e}")
        self.current_process = None

    def _ensure_black_image(self) -> Path:
        """Гарантирует физическое существование эталонного черного изображения black.png."""
        if self._black_image_path.exists():
            return self._black_image_path

        self._black_image_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import struct
            import zlib
            width, height = 1920, 1080
            raw_data = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
            compressed = zlib.compress(raw_data, 9)

            def chunk(chunk_type, data):
                crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
                return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

            png = b"\x89PNG\r\n\x1a\n"
            png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            png += chunk(b"IDAT", compressed)
            png += chunk(b"IEND", b"")

            with open(self._black_image_path, "wb") as f:
                f.write(png)
            logger.info("Сгенерировано эталонное чёрное изображение 1920x1080: %s", self._black_image_path)
        except Exception as e:
            logger.warning("Не удалось автоматически сгенерировать black.png: %s", e)
        return self._black_image_path

    async def _start_black_stream(self) -> None:
        """
        Запуск процесса трансляции полностью чёрного изображения (рисунка) с тишиной.
        Позволяет поддерживать активный RTSP-поток на MediaMTX при отсутствии видеофайлов,
        паузе или окончании эфира, предотвращая появление ошибки 'Подключение к серверу' на клиентах.
        """
        if self._black_process and self._black_process.returncode is None:
            return

        black_img = self._ensure_black_image()
        settings = self.config_manager.get_settings()
        target_url = settings.rtsp_target_url

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-re",
            "-loop", "1",
            "-i", str(black_img.resolve()),
            "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=stereo",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-g", "50",
            "-keyint_min", "25",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "64k",
            "-ar", "48000",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            target_url,
        ]

        try:
            self._black_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("Запущена фоновая трансляция чёрного изображения (black.png) в RTSP.")
            asyncio.create_task(self._log_process_stderr(self._black_process, "black_screen"))
        except Exception as e:
            logger.debug("Не удалось запустить трансляцию чёрного экрана FFmpeg: %s", e)
            self._black_process = None

    async def _stop_black_stream(self) -> None:
        """Остановка трансляции чёрного изображения перед стартом видеофайла."""
        proc = self._black_process
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except Exception:
                pass
        self._black_process = None

    async def _log_process_stderr(
        self, proc: asyncio.subprocess.Process, track_name: str
    ) -> None:
        """Асинхронное чтение stderr для диагностики и предотвращения переполнения буфера pipe."""
        if not proc.stderr:
            return
        try:
            while not proc.stderr.at_eof():
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text and ("error" in text.lower() or "fatal" in text.lower()):
                    logger.warning(f"[FFmpeg {track_name}] {text}")
        except Exception:
            pass

    async def _streaming_loop(self) -> None:
        """Основной бесконечный цикл воспроизведения очереди."""
        while self._is_running:
            # 1. Проверка состояния предохранителя (Circuit Breaker)
            if self.status == StreamStatus.CRITICAL_ERROR:
                self._resume_event.clear()
                logger.critical(
                    "Вещание заблокировано предохранителем! Ожидание ручного сброса через веб-панель..."
                )
                await self._start_black_stream()
                await self._resume_event.wait()
                if not self._is_running:
                    break

            # 2. Получение текущего файла для воспроизведения
            target_file = await self.playlist_manager.get_current()

            if not target_file:
                self.status = StreamStatus.IDLE
                self.current_file = None
                # Очередь пуста или вещание не начато — транслируем полностью чёрное изображение (рисунок)
                await self._start_black_stream()
                # Ждем появления файлов или вызова play_track
                try:
                    await asyncio.wait_for(self._resume_event.wait(), timeout=2.0)
                    self._resume_event.clear()
                except asyncio.TimeoutError:
                    pass
                continue

            # Файл найден — завершаем трансляцию черного экрана перед началом видео
            await self._stop_black_stream()

            # Проверяем физическое наличие файла перед запуском
            if not target_file.exists():
                logger.warning(
                    f"Файл {target_file.name} отсутствует на диске. Пропуск..."
                )
                await self.playlist_manager.advance()
                continue

            self.current_file = target_file
            settings = self.config_manager.get_settings()
            cb_config = settings.circuit_breaker

            # 3. Сборка команды FFmpeg
            filter_complex, extra_inputs, map_args = (
                self.plugin_manager.build_pipeline()
            )

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags",
                "+genpts",
                "-re",
                "-i",
                str(target_file.resolve()),
            ]

            # Добавляем сторонние входы оверлеев (логотип, pip и т.д.)
            cmd.extend(extra_inputs)

            if filter_complex:
                cmd.extend(["-filter_complex", filter_complex])

            cmd.extend(map_args)

            # Параметры кодирования и вещания в MediaMTX
            cmd.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-tune",
                    "zerolatency",
                    "-g",
                    "50",
                    "-keyint_min",
                    "25",
                    "-pix_fmt",
                    "yuv420p",
                    "-maxrate",
                    "6000k",
                    "-bufsize",
                    "6000k",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-ar",
                    "48000",
                    "-af",
                    "aresample=async=1000:min_hard_comp=0.100000:first_pts=0",
                    "-f",
                    "rtsp",
                    "-rtsp_transport",
                    "tcp",
                    settings.rtsp_target_url,
                ]
            )

            logger.info(
                f"Начало трансляции трека: {target_file.name} -> {settings.rtsp_target_url}"
            )
            self.status = StreamStatus.LIVE
            self._skip_requested = False
            start_time = time.time()

            try:
                # Запуск подпроцесса FFmpeg
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.current_process = proc

                # Фоновая задача чтения stderr
                stderr_task = asyncio.create_task(
                    self._log_process_stderr(proc, target_file.name)
                )

                # Периодическая проверка успешности (>15 сек) для сброса consecutive_errors
                reset_done = False
                while proc.returncode is None:
                    await asyncio.sleep(1.0)
                    elapsed = time.time() - start_time
                    if (
                        not reset_done
                        and elapsed >= cb_config.healthy_playback_threshold_sec
                    ):
                        if self.consecutive_errors > 0:
                            logger.info(
                                f"Файл {target_file.name} успешно вещается более {cb_config.healthy_playback_threshold_sec:.0f} сек. Сброс счетчика сбоев."
                            )
                            self.consecutive_errors = 0
                        reset_done = True

                    # Проверяем, завершился ли процесс
                    if proc.returncode is not None:
                        break

                await stderr_task

            except Exception as e:
                logger.error(
                    f"Ошибка при создании/управлении процессом FFmpeg для {target_file.name}: {e}"
                )
                return_code = -1
            else:
                return_code = proc.returncode if proc else 0

            self.current_process = None
            elapsed_total = time.time() - start_time

            # 4. Обработка результатов завершения процесса
            if self._manual_switch_requested:
                logger.info("Выполнен ручной выбор трека. Переход к выбранному треку без advance().")
                self._manual_switch_requested = False
                self._skip_requested = False
                continue

            if self._skip_requested:
                logger.info(
                    f"Файл {target_file.name} пропущен по команде пользователя."
                )
                self._skip_requested = False
                await self.playlist_manager.advance()
                continue

            if return_code != 0:
                # Аварийное завершение FFmpeg
                self.consecutive_errors += 1
                logger.warning(
                    f"Сбой воспроизведения файла {target_file.name} (код выхода: {return_code}). "
                    f"Сбоев подряд: {self.consecutive_errors}/{cb_config.max_consecutive_errors}"
                )

                if self.consecutive_errors >= cb_config.max_consecutive_errors:
                    self.status = StreamStatus.CRITICAL_ERROR
                    logger.critical(
                        f"АВАРИЙНЫЙ ОСТАНОВ: Превышен лимит ошибок подряд ({cb_config.max_consecutive_errors}). "
                        "Circuit Breaker переведен в состояние CRITICAL_ERROR."
                    )
                # Пропускаем проблемный файл, чтобы не крутить его бесконечно
                await self.playlist_manager.advance()
            else:
                # Успешное штатное окончание воспроизведения файла
                logger.info(
                    f"Воспроизведение файла {target_file.name} завершено штатно ({elapsed_total:.1f} сек)."
                )
                if (
                    elapsed_total >= cb_config.healthy_playback_threshold_sec
                    and self.consecutive_errors > 0
                ):
                    self.consecutive_errors = 0

                # Переход к следующему файлу в очереди
                await self.playlist_manager.advance()

            # Небольшая пауза между файлами для переинициализации
            await asyncio.sleep(0.5)

    def get_telemetry(self) -> Dict[str, Any]:
        """Возвращает актуальное состояние оркестратора для API."""
        settings = self.config_manager.get_settings()
        return {
            "status": self.status.value,
            "current_file": self.current_file.name if self.current_file else None,
            "consecutive_errors": self.consecutive_errors,
            "max_consecutive_errors": settings.circuit_breaker.max_consecutive_errors,
            "target_rtsp_url": settings.rtsp_target_url,
            "hls_url": settings.mediamtx_hls_url,
        }
