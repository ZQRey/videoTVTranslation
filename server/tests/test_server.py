"""
Модульные и интеграционные тесты медиасервера непрерывного вещания.
Проверяют:
1. Логику динамической очереди PlaylistManager (вставка за текущим, удаление, зацикливание).
2. Работу ConfigManager (валидация, слияние, сохранение).
3. Сборку цепочки оверлеев PluginManager.
4. Поведение предохранителя Circuit Breaker.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.config import ClockFormat, ConfigManager, Position, ServerSettings
from core.playlist import PlaylistManager
from core.plugins.manager import PluginManager
from core.streamer import StreamOrchestrator, StreamStatus


class TestPlaylistManager(unittest.IsolatedAsyncioTestCase):
    """Тестирование строгого алгоритма очереди воспроизведения."""

    async def asyncSetUp(self):
        self.mgr = PlaylistManager()

    async def test_initial_sync(self):
        """Проверка первичного заполнения очереди."""
        files = [Path("video1.mp4"), Path("video2.mp4"), Path("video3.mp4")]
        await self.mgr.sync_with_scanned(files)

        state = await self.mgr.get_state()
        self.assertEqual(state["total"], 3)
        self.assertEqual(state["current_index"], 0)
        self.assertEqual(state["current_file"], "video1.mp4")

    async def test_insert_new_files_immediately_after_current(self):
        """
        СТРОГОЕ ТРЕБОВАНИЕ: Новые файлы должны вставляться СРАЗУ ЗА ТЕКУЩИМ
        воспроизводимым файлом, а не в конец очереди.
        """
        initial_files = [Path("A.mp4"), Path("B.mp4"), Path("C.mp4")]
        await self.mgr.sync_with_scanned(initial_files)

        # Текущий файл: A (индекс 0)
        current = await self.mgr.get_current()
        self.assertEqual(current.name, "A.mp4")

        # Появились новые файлы: D и E
        scanned_files = [
            Path("A.mp4"),
            Path("B.mp4"),
            Path("C.mp4"),
            Path("D.mp4"),
            Path("E.mp4"),
        ]
        await self.mgr.sync_with_scanned(scanned_files)

        state = await self.mgr.get_state()
        # Очередь должна стать: A, D, E, B, C
        self.assertEqual(
            state["items"], ["A.mp4", "D.mp4", "E.mp4", "B.mp4", "C.mp4"]
        )
        self.assertEqual(state["current_index"], 0)

        # Следующий трек после A должен быть D!
        next_track = await self.mgr.advance()
        self.assertEqual(next_track.name, "D.mp4")

        # Затем E
        next_track2 = await self.mgr.advance()
        self.assertEqual(next_track2.name, "E.mp4")

        # Затем исходный B
        next_track3 = await self.mgr.advance()
        self.assertEqual(next_track3.name, "B.mp4")

    async def test_file_removal(self):
        """Проверка удаления файла из очереди при его исчезновении с диска."""
        files = [Path("1.mp4"), Path("2.mp4"), Path("3.mp4")]
        await self.mgr.sync_with_scanned(files)

        # Переходим ко 2-му треку
        await self.mgr.advance()
        self.assertEqual((await self.mgr.get_current()).name, "2.mp4")

        # Файл 1 удален с диска
        await self.mgr.sync_with_scanned([Path("2.mp4"), Path("3.mp4")])
        state = await self.mgr.get_state()
        self.assertEqual(state["items"], ["2.mp4", "3.mp4"])
        # Указатель должен скорректироваться и продолжать указывать на 2.mp4
        self.assertEqual((await self.mgr.get_current()).name, "2.mp4")

    async def test_queue_looping(self):
        """Проверка зацикливания очереди по кругу при достижении конца."""
        files = [Path("track1.mp4"), Path("track2.mp4")]
        await self.mgr.sync_with_scanned(files)

        self.assertEqual((await self.mgr.get_current()).name, "track1.mp4")
        t2 = await self.mgr.advance()
        self.assertEqual(t2.name, "track2.mp4")
        # После track2 очередь должна вернуться на track1
        t1 = await self.mgr.advance()
        self.assertEqual(t1.name, "track1.mp4")


class TestConfigManager(unittest.IsolatedAsyncioTestCase):
    """Тестирование ConfigManager."""

    async def test_update_and_save(self):
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = Path(temp_dir) / "test_settings.json"
            cfg_mgr = ConfigManager(cfg_path)

            initial = cfg_mgr.get_settings()
            self.assertEqual(initial.scan_interval, 10)

            # Обновление настроек
            updated = await cfg_mgr.update_settings(
                {
                    "scan_interval": 15,
                    "plugins": {
                        "clock": {
                            "enabled": True,
                            "format": "HH:mm",
                        }
                    },
                }
            )

            self.assertEqual(updated.scan_interval, 15)
            self.assertTrue(updated.plugins.clock.enabled)
            self.assertEqual(updated.plugins.clock.format, ClockFormat.TIME_SHORT)

            # Проверка, что файл сохранился на диск
            self.assertTrue(cfg_path.exists())
            # Перезагружаем менеджер
            reloaded_mgr = ConfigManager(cfg_path)
            self.assertEqual(reloaded_mgr.get_settings().scan_interval, 15)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestPluginManager(unittest.IsolatedAsyncioTestCase):
    """Тестирование сборки цепочки -filter_complex."""

    async def test_no_plugins_enabled(self):
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = Path(temp_dir) / "test_settings.json"
            cfg_mgr = ConfigManager(cfg_path)
            mgr = PluginManager(cfg_mgr)

            fc, extra, maps = mgr.build_pipeline()
            self.assertIsNone(fc)
            self.assertEqual(extra, [])
            self.assertEqual(maps, ["-map", "0:v", "-map", "0:a?"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def test_clock_and_pip_plugins(self):
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = Path(temp_dir) / "test_settings.json"
            cfg_mgr = ConfigManager(cfg_path)
            await cfg_mgr.update_settings(
                {
                    "plugins": {
                        "clock": {"enabled": True, "position": "top_left"},
                        "pip": {
                            "enabled": True,
                            "stream_url": "rtsp://camera:8554/live",
                        },
                    }
                }
            )
            mgr = PluginManager(cfg_mgr)

            fc, extra, maps = mgr.build_pipeline()
            self.assertIsNotNone(fc)
            # Должен быть вход для PiP
            self.assertIn("rtsp://camera:8554/live", extra)
            # Должен быть выход [outv]
            self.assertEqual(maps, ["-map", "[outv]", "-map", "0:a?"])
            self.assertIn("drawtext", fc)
            self.assertIn("overlay", fc)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    """Тестирование логики предохранителя Circuit Breaker."""

    async def test_circuit_breaker_reset(self):
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = Path(temp_dir) / "test_settings.json"
            cfg_mgr = ConfigManager(cfg_path)
            pm = PlaylistManager()
            pl = PluginManager(cfg_mgr)
            streamer = StreamOrchestrator(cfg_mgr, pm, pl)

            streamer.consecutive_errors = 10
            streamer.status = StreamStatus.CRITICAL_ERROR

            # Ручной сброс
            await streamer.reset_circuit_breaker()
            self.assertEqual(streamer.consecutive_errors, 0)
            self.assertEqual(streamer.status, StreamStatus.IDLE)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
