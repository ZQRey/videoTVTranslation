"""
Модульные и интеграционные тесты управления воспроизведением и удаления файлов.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.config import ConfigManager, ServerSettings
from core.playlist import PlaylistManager
from core.plugins.manager import PluginManager
from core.streamer import StreamOrchestrator
from main import (
    DeleteFileRequest,
    MovePlaylistItemRequest,
    PlayTrackRequest,
    ReorderPlaylistRequest,
    delete_media_file_endpoint,
    move_playlist_item,
    play_specific_track,
    playlist_mgr,
    previous_track,
    reorder_playlist,
    streamer,
)


class TestPlaylistControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mgr = PlaylistManager()
        self.files = [Path("1.mp4"), Path("2.mp4"), Path("3.mp4")]
        await self.mgr.sync_with_scanned(self.files)

    async def test_set_current_by_name(self):
        """Проверка выбора конкретного трека по имени."""
        res = await self.mgr.set_current_by_name("3.mp4")
        self.assertIsNotNone(res)
        self.assertEqual(res.name, "3.mp4")

        state = await self.mgr.get_state()
        self.assertEqual(state["current_index"], 2)
        self.assertEqual(state["current_file"], "3.mp4")

        # Несуществующий файл
        not_found = await self.mgr.set_current_by_name("non_existent.mp4")
        self.assertIsNone(not_found)

    async def test_previous_track(self):
        """Проверка циклического перехода к предыдущему треку."""
        # Начальный индекс 0 (1.mp4)
        prev = await self.mgr.previous()
        # Должен зациклиться на последний элемент (3.mp4, индекс 2)
        self.assertEqual(prev.name, "3.mp4")

        state = await self.mgr.get_state()
        self.assertEqual(state["current_index"], 2)

        # Еще раз назад -> 2.mp4 (индекс 1)
        prev2 = await self.mgr.previous()
        self.assertEqual(prev2.name, "2.mp4")

    async def test_move_item(self):
        """Проверка перемещения трека на новую позицию."""
        # Перемещаем 3.mp4 (индекс 2) на первое место (индекс 0)
        success = await self.mgr.move_item(2, 0)
        self.assertTrue(success)

        state = await self.mgr.get_state()
        self.assertEqual(state["items"], ["3.mp4", "1.mp4", "2.mp4"])
        # Текущий файл (был 1.mp4) теперь имеет индекс 1
        self.assertEqual(state["current_index"], 1)
        self.assertEqual(state["current_file"], "1.mp4")

        # Некорректные индексы
        self.assertFalse(await self.mgr.move_item(-1, 0))
        self.assertFalse(await self.mgr.move_item(0, 10))

    async def test_reorder(self):
        """Проверка пакетного изменения порядка воспроизведения."""
        new_order = ["2.mp4", "3.mp4", "1.mp4"]
        success = await self.mgr.reorder(new_order)
        self.assertTrue(success)

        state = await self.mgr.get_state()
        self.assertEqual(state["items"], new_order)
        # Текущий файл (1.mp4) теперь на индексе 2
        self.assertEqual(state["current_index"], 2)
        self.assertEqual(state["current_file"], "1.mp4")

        # Некорректный состав списка
        self.assertFalse(await self.mgr.reorder(["1.mp4", "2.mp4"]))
        self.assertFalse(await self.mgr.reorder(["1.mp4", "2.mp4", "unknown.mp4"]))

    async def test_remove_file(self):
        """Проверка удаления файла из очереди с корректировкой указателя."""
        # Удаляем 1.mp4 (текущий)
        removed = await self.mgr.remove_file("1.mp4")
        self.assertIsNotNone(removed)
        self.assertEqual(removed.name, "1.mp4")

        state = await self.mgr.get_state()
        self.assertEqual(state["total"], 2)
        self.assertEqual(state["items"], ["2.mp4", "3.mp4"])
        self.assertEqual(state["current_file"], "2.mp4")


class TestFileDeletionAndOrchestrator(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_file = Path(self.test_dir) / "settings.json"
        self.media_dir = Path(self.test_dir) / "test_media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # Создаем тестовые файлы
        self.f1 = self.media_dir / "clip1.mp4"
        self.f2 = self.media_dir / "clip2.mp4"
        self.f1.write_text("dummy video 1")
        self.f2.write_text("dummy video 2")

        self.cfg_mgr = ConfigManager(self.config_file)
        await self.cfg_mgr.update_settings({"media_dir": str(self.media_dir)})

        self.playlist = PlaylistManager()
        await self.playlist.sync_with_scanned([self.f1, self.f2])

        self.plugin_mgr = PluginManager(self.cfg_mgr)
        self.streamer = StreamOrchestrator(self.cfg_mgr, self.playlist, self.plugin_mgr)

    async def asyncTearDown(self):
        await self.streamer.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_play_and_previous_orchestrator(self):
        """Проверка вызова play_track и previous_track в оркестраторе."""
        success = await self.streamer.play_track("clip2.mp4")
        self.assertTrue(success)
        self.assertTrue(self.streamer._manual_switch_requested)

        cur = await self.playlist.get_current()
        self.assertEqual(cur.name, "clip2.mp4")

        # Previous
        prev_ok = await self.streamer.previous_track()
        self.assertTrue(prev_ok)
        cur2 = await self.playlist.get_current()
        self.assertEqual(cur2.name, "clip1.mp4")

    async def test_delete_media_file_security(self):
        """Проверка защиты от Path Traversal при удалении."""
        with self.assertRaises(ValueError):
            await self.streamer.delete_media_file("../secret.txt")

        with self.assertRaises(ValueError):
            await self.streamer.delete_media_file("sub/file.mp4")

    async def test_delete_media_file_success(self):
        """Проверка физического удаления файла с диска и исключения из очереди."""
        self.assertTrue(self.f1.exists())

        deleted = await self.streamer.delete_media_file("clip1.mp4")
        self.assertTrue(deleted)
        self.assertFalse(self.f1.exists())

        state = await self.playlist.get_state()
        self.assertEqual(state["total"], 1)
        self.assertNotIn("clip1.mp4", state["items"])


class TestPlaylistApiEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Добавляем тестовые элементы в глобальный playlist_mgr
        await playlist_mgr.sync_with_scanned([Path("api_v1.mp4"), Path("api_v2.mp4"), Path("api_v3.mp4")])

    async def test_api_play_and_previous(self):
        """Проверка API эндпоинтов /api/player/play-track и /api/player/previous."""
        res_play = await play_specific_track(PlayTrackRequest(filename="api_v2.mp4"))
        self.assertTrue(res_play["success"])

        state = await playlist_mgr.get_state()
        self.assertEqual(state["current_file"], "api_v2.mp4")

        res_prev = await previous_track()
        self.assertTrue(res_prev["success"])
        state_after = await playlist_mgr.get_state()
        self.assertEqual(state_after["current_file"], "api_v1.mp4")

    async def test_api_move_and_reorder(self):
        """Проверка API эндпоинтов /api/playlist/move и /api/playlist/reorder."""
        move_res = await move_playlist_item(MovePlaylistItemRequest(from_index=0, to_index=2))
        self.assertTrue(move_res["success"])
        self.assertEqual(move_res["playlist"]["items"], ["api_v2.mp4", "api_v3.mp4", "api_v1.mp4"])

        reorder_res = await reorder_playlist(ReorderPlaylistRequest(items=["api_v3.mp4", "api_v1.mp4", "api_v2.mp4"]))
        self.assertTrue(reorder_res["success"])
        self.assertEqual(reorder_res["playlist"]["items"], ["api_v3.mp4", "api_v1.mp4", "api_v2.mp4"])


if __name__ == "__main__":
    unittest.main()
