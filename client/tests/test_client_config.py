"""
Модульные тесты конфигурации клиента (client/config.py).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
client_dir = Path(__file__).resolve().parent.parent
if str(client_dir) not in sys.path:
    sys.path.insert(0, str(client_dir))

from config import ClientConfig, ConfigManager


class TestClientConfig(unittest.TestCase):
    """Тесты модели ClientConfig."""

    def test_default_values(self):
        cfg = ClientConfig()
        self.assertEqual(cfg.server_host, "")
        self.assertEqual(cfg.rtsp_port, 8554)
        self.assertEqual(cfg.stream_path, "live")
        self.assertEqual(cfg.network_caching, 1000)
        self.assertFalse(cfg.is_configured())

    def test_rtsp_url_generation(self):
        cfg = ClientConfig(
            server_host="192.168.1.150",
            rtsp_port=8554,
            stream_path="test_stream"
        )
        self.assertEqual(cfg.rtsp_url, "rtsp://192.168.1.150:8554/test_stream")
        self.assertTrue(cfg.is_configured())

    def test_clean_path_and_host(self):
        cfg = ClientConfig(
            server_host="   10.0.0.5  ",
            stream_path="///custom_live/"
        )
        self.assertEqual(cfg.server_host, "10.0.0.5")
        self.assertEqual(cfg.stream_path, "custom_live/")
        self.assertEqual(cfg.rtsp_url, "rtsp://10.0.0.5:8554/custom_live/")


class TestConfigManager(unittest.TestCase):
    """Тесты менеджера конфигурации ConfigManager."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = Path(self.test_dir) / "test_client_config.json"

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_non_existing_file(self):
        mgr = ConfigManager(config_path=self.config_path)
        cfg = mgr.config
        self.assertEqual(cfg.server_host, "")
        self.assertFalse(cfg.is_configured())

    def test_save_and_reload(self):
        mgr = ConfigManager(config_path=self.config_path)
        new_cfg = ClientConfig(
            server_host="127.0.0.1",
            rtsp_port=8554,
            stream_path="live",
            network_caching=500
        )
        saved = mgr.save(new_cfg)
        self.assertTrue(saved)
        self.assertTrue(self.config_path.exists())

        # Чтение напрямую
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["server_host"], "127.0.0.1")
        self.assertEqual(data["network_caching"], 500)

        # Создание нового менеджера для проверки загрузки
        mgr2 = ConfigManager(config_path=self.config_path)
        self.assertEqual(mgr2.config.server_host, "127.0.0.1")
        self.assertEqual(mgr2.config.network_caching, 500)
        self.assertTrue(mgr2.config.is_configured())

    def test_update_method(self):
        mgr = ConfigManager(config_path=self.config_path)
        cfg = mgr.update(server_host="my-streaming-server.lan", rtsp_port=9000, stream_path="hd_stream")
        self.assertEqual(cfg.server_host, "my-streaming-server.lan")
        self.assertEqual(cfg.rtsp_port, 9000)
        self.assertEqual(cfg.stream_path, "hd_stream")
        self.assertEqual(cfg.rtsp_url, "rtsp://my-streaming-server.lan:9000/hd_stream")

    def test_corrupted_file_fallback(self):
        # Записываем битый JSON
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("{invalid-json-content")

        mgr = ConfigManager(config_path=self.config_path)
        self.assertEqual(mgr.config.server_host, "")
        self.assertEqual(mgr.config.rtsp_port, 8554)


if __name__ == "__main__":
    unittest.main()
