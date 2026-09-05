"""
Тесты для фонового вещания черного изображения (black.png) и публичного API Android TV.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.client_manager import client_manager
from core.streamer import StreamOrchestrator
from main import get_client_public_status


class TestBlackStreamLogic(unittest.IsolatedAsyncioTestCase):
    """Тестирование генерации черного экрана и логики вещания заглушки."""

    def test_ensure_black_image_creates_valid_png(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_img_path = Path(tmpdir) / "sub" / "black.png"
            streamer = StreamOrchestrator(
                config_manager=MagicMock(),
                playlist_manager=MagicMock(),
                plugin_manager=MagicMock(),
            )
            streamer._black_image_path = test_img_path

            result = streamer._ensure_black_image()
            self.assertTrue(result.exists())
            self.assertGreater(result.stat().st_size, 100)
            with open(result, "rb") as f:
                header = f.read(8)
                self.assertEqual(header, b"\x89PNG\r\n\x1a\n", "Файл должен быть валидным PNG")

    async def test_black_stream_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_img_path = Path(tmpdir) / "black.png"
            mock_cfg = MagicMock()
            mock_cfg.get_settings.return_value.rtsp_target_url = "rtsp://localhost:8554/live"

            streamer = StreamOrchestrator(
                config_manager=mock_cfg,
                playlist_manager=MagicMock(),
                plugin_manager=MagicMock(),
            )
            streamer._black_image_path = test_img_path

            mock_proc = MagicMock()
            mock_proc.returncode = None
            mock_proc.wait = AsyncMock()

            with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
                # 1. Запуск черного потока
                await streamer._start_black_stream()
                self.assertIsNotNone(streamer._black_process)
                mock_exec.assert_called_once()
                cmd_args = mock_exec.call_args[0]
                self.assertIn("-loop", cmd_args)
                self.assertTrue(any("anullsrc" in str(arg) for arg in cmd_args))

                # Повторный вызов не должен плодить дублирующие процессы
                await streamer._start_black_stream()
                self.assertEqual(mock_exec.call_count, 1)

                # 2. Остановка черного потока
                await streamer._stop_black_stream()
                self.assertIsNone(streamer._black_process)
                mock_proc.terminate.assert_called_once()


class TestAndroidPublicStatusApi(unittest.IsolatedAsyncioTestCase):
    """Тестирование автоматической регистрации и расписания через /api/client/status."""

    async def test_android_registration_and_schedule_update(self):
        # 1. Регистрация нового Android-клиента через query params
        req = MagicMock()
        req.client.host = "192.168.1.177"

        status = await get_client_public_status(
            request=req,
            client_id="android-tv-box-1",
            token="android-token-unique-123",
            ip="192.168.1.177",
            hostname="Xiaomi-TV-Box",
            os_info="Android 11 (API 30, MiBox4)",
            schedule_mode="interval",
            schedule_start="06:00",
            schedule_end="23:00",
            schedule_days="1,2,3,4,5,6,7",
        )

        self.assertEqual(status["client_id"], "android-tv-box-1")
        self.assertEqual(status["token"], "android-token-unique-123")
        self.assertEqual(status["schedule_mode"], "interval")
        self.assertEqual(status["schedule_start"], "06:00")
        self.assertEqual(status["schedule_end"], "23:00")

        # Клиент должен присутствовать в client_manager
        client = client_manager.get_client("android-tv-box-1")
        self.assertIsNotNone(client)
        self.assertEqual(client.token, "android-token-unique-123")
        self.assertEqual(client.schedule_days, [1, 2, 3, 4, 5, 6, 7])

        # 2. Обновление параметров существующего клиента
        status_updated = await get_client_public_status(
            request=req,
            token="android-token-unique-123",
            schedule_mode="24/7",
        )
        self.assertEqual(status_updated["schedule_mode"], "24/7")
        self.assertFalse(status_updated["standby"])


if __name__ == "__main__":
    unittest.main()
