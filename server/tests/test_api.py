"""
Интеграционные тесты обработчиков FastAPI эндпоинтов.
"""

import unittest

from main import (
    get_configuration,
    get_system_status,
    reset_circuit_breaker,
    skip_current_track,
    trigger_rescan,
    update_configuration,
)


class TestFastAPIHandlers(unittest.IsolatedAsyncioTestCase):
    async def test_status_endpoint(self):
        """Проверка эндпоинта комплексной телеметрии /api/status."""
        data = await get_system_status()
        self.assertIn("streamer", data)
        self.assertIn("playlist", data)
        self.assertIn("plugins", data)
        self.assertIn("status", data["streamer"])
        self.assertIn("total", data["playlist"])

    async def test_scan_trigger(self):
        """Проверка принудительного сканирования /api/player/scan."""
        res = await trigger_rescan()
        self.assertTrue(res.get("success"))

    async def test_config_get_and_update(self):
        """Проверка чтения и обновления настроек."""
        initial_cfg = await get_configuration()
        self.assertIn("media_dir", initial_cfg)

        # Обновление scan_interval
        update_res = await update_configuration({"scan_interval": 14})
        self.assertTrue(update_res.get("success"))
        self.assertEqual(update_res["settings"]["scan_interval"], 14)

    async def test_player_reset_breaker(self):
        """Проверка сброса предохранителя через API."""
        res = await reset_circuit_breaker()
        self.assertTrue(res.get("success"))

    async def test_player_skip(self):
        """Проверка пропуска трека через API."""
        res = await skip_current_track()
        self.assertTrue(res.get("success"))


if __name__ == "__main__":
    unittest.main()
