"""
Тесты динамической системы плагинов:
- Создание визуальных плагинов (текст, бегущая строка, видеофильтры, баннеры).
- Загрузка и валидация кастомного Python-плагина.
- Динамическое удаление плагинов.
- REST API маршруты управления плагинами.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core.config import ConfigManager
from core.plugins.custom_filter import CustomFilterOverlayPlugin
from core.plugins.custom_image import CustomImageOverlayPlugin
from core.plugins.custom_text import TextTickerOverlayPlugin
from core.plugins.manager import PluginManager
from main import create_visual_plugin, delete_custom_plugin, get_plugin_templates, upload_python_plugin


class TestCustomPlugins(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_path = Path(self.temp_dir) / "settings.json"
        self.cfg_mgr = ConfigManager(self.cfg_path)
        self.plugin_mgr = PluginManager(self.cfg_mgr)

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_text_ticker_plugin_filter_generation(self):
        """Проверка генерации фильтра для бегущей строки."""
        plugin = TextTickerOverlayPlugin(
            name="ticker_news",
            title="Новости",
            config_manager=self.cfg_mgr,
            default_config={
                "enabled": True,
                "text": "Тестовые новости 24/7",
                "mode": "scroll",
                "speed": 150,
                "position": "bottom",
            },
        )
        self.assertTrue(plugin.is_enabled())
        filter_str, extra = plugin.build_filter("0:v", "outv", [])
        self.assertEqual(extra, [])
        self.assertIn("drawtext", filter_str)
        self.assertIn("x=w-mod(t*150\\,w+tw)", filter_str)
        self.assertIn("Тестовые новости 24/7", filter_str)

    async def test_custom_filter_plugin_generation(self):
        """Проверка генерации фильтра для видеоэффекта."""
        plugin = CustomFilterOverlayPlugin(
            name="vintage_look",
            title="Ретро",
            config_manager=self.cfg_mgr,
            default_config={
                "enabled": True,
                "filter_expr": "curves=vintage",
            },
        )
        self.assertTrue(plugin.is_enabled())
        filter_str, extra = plugin.build_filter("0:v", "outv", [])
        self.assertEqual(extra, [])
        self.assertEqual(filter_str, "[0:v]curves=vintage[outv]")

    async def test_register_and_unregister_visual_plugin(self):
        """Проверка регистрации и удаления визуального плагина через PluginManager."""
        plugin = await self.plugin_mgr.register_visual_plugin(
            plugin_type="text_ticker",
            name="breaking_news",
            title="Срочные новости",
            initial_config={"enabled": True, "text": "Алерты и события"},
        )
        self.assertIn("breaking_news", self.plugin_mgr.plugins)
        self.assertTrue(self.plugin_mgr.plugins["breaking_news"].is_enabled())

        # Удаление
        deleted = await self.plugin_mgr.unregister_custom_plugin("breaking_news")
        self.assertTrue(deleted)
        self.assertNotIn("breaking_news", self.plugin_mgr.plugins)

    async def test_install_python_plugin_code(self):
        """Проверка установки Python-плагина из исходного кода."""
        code = '''
from core.plugins.base import BaseOverlayPlugin

class DynamicSepiaPlugin(BaseOverlayPlugin):
    def __init__(self, config_manager=None):
        super().__init__(name="dynamic_sepia")
        self.config_manager = config_manager
        self.is_custom = True
        self.title = "Сепия эффект"

    def is_enabled(self):
        return True

    def get_settings_schema(self):
        return {"name": self.name, "title": self.title, "enabled": True}

    def build_filter(self, input_label, output_label, extra_inputs):
        return f"[{input_label}]colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131[{output_label}]", extra_inputs
'''
        plugin = await self.plugin_mgr.install_python_plugin_code(
            name="dynamic_sepia", code=code
        )
        self.assertIn("dynamic_sepia", self.plugin_mgr.plugins)
        self.assertEqual(plugin.title, "Сепия эффект")

        filter_str, extra = plugin.build_filter("0:v", "outv", [])
        self.assertIn("colorchannelmixer", filter_str)

        # Очистка
        await self.plugin_mgr.unregister_custom_plugin("dynamic_sepia")
        self.assertNotIn("dynamic_sepia", self.plugin_mgr.plugins)

    async def test_api_create_and_delete_visual_plugin(self):
        """Проверка REST API эндпоинтов создания и удаления визуального плагина."""
        res = await create_visual_plugin(
            {
                "plugin_type": "text_ticker",
                "name": "api_ticker",
                "title": "API Бегущая строка",
                "config": {"enabled": True, "text": "Тест через API"},
            }
        )
        self.assertTrue(res.get("success"))
        self.assertEqual(res["schema"]["name"], "api_ticker")

        # Проверка удаления через API
        del_res = await delete_custom_plugin("api_ticker")
        self.assertTrue(del_res.get("success"))

    async def test_api_get_templates(self):
        """Проверка получения шаблонов плагинов."""
        templates = await get_plugin_templates()
        self.assertIn("python_starter", templates)
        self.assertIn("visual_presets", templates)
        self.assertIn("text_ticker", templates["visual_presets"])


if __name__ == "__main__":
    unittest.main()
