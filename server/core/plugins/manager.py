"""
Менеджер плагинов оверлеев с поддержкой динамического добавления,
загрузки и удаления плагинов через веб-интерфейс и REST API.
"""

import ast
import importlib.util
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from core.config import ConfigManager
from core.plugins.base import BaseOverlayPlugin
from core.plugins.clock import ClockOverlayPlugin
from core.plugins.custom_filter import CustomFilterOverlayPlugin
from core.plugins.custom_image import CustomImageOverlayPlugin
from core.plugins.custom_text import TextTickerOverlayPlugin
from core.plugins.logo import LogoOverlayPlugin
from core.plugins.pip import PipOverlayPlugin

logger = logging.getLogger("stream_server.plugins.manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOM_PLUGINS_DIR = BASE_DIR / "plugins_custom"

STARTER_PYTHON_PLUGIN_TEMPLATE = '''"""
Пользовательский оверлей-плагин для Continuous Broadcast Media Server.
"""

from typing import Tuple, List, Dict, Any
from core.plugins.base import BaseOverlayPlugin

class MyCustomOverlayPlugin(BaseOverlayPlugin):
    """
    Пример кастомного плагина: накладывает надпись или модифицирует видеопоток.
    """
    def __init__(self, config_manager=None):
        super().__init__(name="my_custom_plugin")
        self.config_manager = config_manager
        self.is_custom = True
        self.title = "Мой кастомный плагин"

    def is_enabled(self) -> bool:
        """Возвращает True, если плагин активен."""
        if not self.config_manager:
            return True
        settings = self.config_manager.get_settings()
        cfg = getattr(settings.plugins, self.name, {})
        if isinstance(cfg, dict):
            return cfg.get("enabled", True)
        return getattr(cfg, "enabled", True)

    def get_settings_schema(self) -> Dict[str, Any]:
        """Возвращает схему настроек плагина для отображения в веб-панели."""
        return {
            "name": self.name,
            "title": self.title,
            "is_custom": True,
            "plugin_type": "python",
            "enabled": self.is_enabled(),
            "custom_text": "Live Broadcast HD",
        }

    def build_filter(
        self,
        input_label: str,
        output_label: str,
        extra_inputs: List[str]
    ) -> Tuple[str, List[str]]:
        """
        Формирует выражение фильтра FFmpeg.
        input_label: входной поток (например, "0:v" или "v_step_0")
        output_label: выходной поток (например, "outv")
        extra_inputs: список дополнительных -i путей/URL
        """
        # Пример: добавляем плавную виньетку и легкое увеличение контраста
        filter_expr = f"[{input_label}]eq=contrast=1.08:saturation=1.1[{output_label}]"
        return filter_expr, extra_inputs
'''


class PluginManager:
    """
    Управляет жизненным циклом, динамической регистрацией и сборкой цепочек фильтров.
    """

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        CUSTOM_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

        # 1. Встроенные системные плагины
        self.plugins: Dict[str, BaseOverlayPlugin] = {
            "logo": LogoOverlayPlugin(config_manager),
            "clock": ClockOverlayPlugin(config_manager),
            "pip": PipOverlayPlugin(config_manager),
        }

        # 2. Загрузка зарегистрированных пользовательских плагинов
        self.load_custom_plugins()

    def load_custom_plugins(self) -> None:
        """Загрузка динамических плагинов из конфигурации и папки plugins_custom/."""
        settings = self.config_manager.get_settings()
        custom_meta = getattr(settings, "custom_plugins_meta", {})

        # А. Загрузка визуальных плагинов из метаданных
        for name, meta in custom_meta.items():
            if name in self.plugins:
                continue

            source = meta.get("source", "visual")
            ptype = meta.get("type", "text_ticker")
            title = meta.get("title", name)

            if source == "visual":
                if ptype == "text_ticker":
                    plugin = TextTickerOverlayPlugin(name, title, self.config_manager)
                    self.plugins[name] = plugin
                    logger.info(f"Загружен пользовательский плагин текста: {name}")
                elif ptype == "filter":
                    plugin = CustomFilterOverlayPlugin(name, title, self.config_manager)
                    self.plugins[name] = plugin
                    logger.info(f"Загружен пользовательский плагин видеофильтра: {name}")
                elif ptype == "image":
                    plugin = CustomImageOverlayPlugin(name, title, self.config_manager)
                    self.plugins[name] = plugin
                    logger.info(f"Загружен пользовательский плагин баннера: {name}")

        # Б. Загрузка Python-модулей из plugins_custom/
        for file_path in CUSTOM_PLUGINS_DIR.glob("*.py"):
            if file_path.name.startswith("__"):
                continue
            try:
                plugin_instance = self._load_python_plugin_from_file(file_path)
                if plugin_instance:
                    self.plugins[plugin_instance.name] = plugin_instance
                    logger.info(
                        f"Успешно загружен Python-плагин: {plugin_instance.name} из {file_path.name}"
                    )
            except Exception as e:
                logger.error(f"Ошибка загрузки плагина из {file_path.name}: {e}")

    def _load_python_plugin_from_file(
        self, file_path: Path
    ) -> Optional[BaseOverlayPlugin]:
        """Динамическая загрузка класса плагина из .py файла через importlib."""
        module_name = f"custom_plugin_{file_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, str(file_path.resolve()))
        if not spec or not spec.loader:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Поиск классов, унаследованных от BaseOverlayPlugin
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseOverlayPlugin) and obj is not BaseOverlayPlugin:
                # Инициализация экземпляра
                try:
                    instance = obj(config_manager=self.config_manager)
                except TypeError:
                    instance = obj()
                instance.is_custom = True
                return instance
        return None

    async def register_visual_plugin(
        self,
        plugin_type: str,
        name: str,
        title: str,
        initial_config: Dict[str, Any],
    ) -> BaseOverlayPlugin:
        """
        Регистрация нового визуального плагина (текст/фильтр/баннер) на лету.
        """
        name = name.strip().lower().replace(" ", "_")
        if name in self.plugins:
            raise ValueError(f"Плагин с именем '{name}' уже существует.")

        if plugin_type == "text_ticker":
            plugin = TextTickerOverlayPlugin(
                name=name,
                title=title,
                config_manager=self.config_manager,
                default_config=initial_config,
            )
        elif plugin_type == "filter":
            plugin = CustomFilterOverlayPlugin(
                name=name,
                title=title,
                config_manager=self.config_manager,
                default_config=initial_config,
            )
        elif plugin_type == "image":
            plugin = CustomImageOverlayPlugin(
                name=name,
                title=title,
                config_manager=self.config_manager,
                default_config=initial_config,
            )
        else:
            raise ValueError(f"Неизвестный тип визуального плагина: {plugin_type}")

        self.plugins[name] = plugin

        # Сохранение в метаданные и настройки
        await self.config_manager.update_settings(
            {
                "custom_plugins_meta": {
                    name: {
                        "name": name,
                        "title": title,
                        "type": plugin_type,
                        "source": "visual",
                    }
                },
                "plugins": {name: initial_config},
            }
        )
        logger.info(f"Зарегистрирован новый визуальный плагин: {name} ({title})")
        return plugin

    async def install_python_plugin_code(
        self,
        name: str,
        code: str,
    ) -> BaseOverlayPlugin:
        """
        Проверка синтаксиса, сохранение на диск и динамическая загрузка Python-плагина.
        """
        name = name.strip().lower().replace(" ", "_")
        if name in ["logo", "clock", "pip"]:
            raise ValueError(f"Имя '{name}' зарезервировано системным плагином.")

        # 1. Валидация синтаксиса через ast
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"Ошибка синтаксиса Python кода: {e}")

        # 2. Сохранение файла
        file_path = CUSTOM_PLUGINS_DIR / f"{name}.py"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)

        # 3. Динамическая загрузка
        plugin_instance = self._load_python_plugin_from_file(file_path)
        if not plugin_instance:
            if file_path.exists():
                file_path.unlink()
            raise ValueError(
                "В предоставленном коде не найден класс, наследующий BaseOverlayPlugin."
            )

        plugin_instance.name = name
        self.plugins[name] = plugin_instance

        # 4. Обновление метаданных
        await self.config_manager.update_settings(
            {
                "custom_plugins_meta": {
                    name: {
                        "name": name,
                        "title": getattr(plugin_instance, "title", name),
                        "type": "python",
                        "source": "python",
                        "file": file_path.name,
                    }
                },
                "plugins": {name: {"enabled": False}},
            }
        )
        logger.info(f"Успешно установлен пользовательский Python-плагин: {name}")
        return plugin_instance

    async def unregister_custom_plugin(self, name: str) -> bool:
        """
        Удаление пользовательского плагина из системы и с диска.
        """
        if name in ["logo", "clock", "pip"]:
            raise ValueError("Нельзя удалить встроенный системный плагин.")

        if name not in self.plugins:
            return False

        # Удаление экземпляра из памяти
        del self.plugins[name]

        # Удаление файла .py если существовал
        py_file = CUSTOM_PLUGINS_DIR / f"{name}.py"
        if py_file.exists():
            try:
                py_file.unlink()
            except OSError:
                pass

        # Очистка из конфигурации и сохранение
        await self.config_manager.remove_custom_plugin(name)
        logger.info(f"Пользовательский плагин {name} успешно удален.")
        return True

    def get_plugin(self, name: str) -> Optional[BaseOverlayPlugin]:
        return self.plugins.get(name)

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Возвращает текущие состояния и схемы всех плагинов для UI."""
        schemas = []
        for plugin in self.plugins.values():
            schema = plugin.get_settings_schema()
            schema["is_custom"] = getattr(plugin, "is_custom", False)
            schemas.append(schema)
        return schemas

    def build_pipeline(
        self,
    ) -> Tuple[Optional[str], List[str], List[str]]:
        """
        Собирает цепочку фильтров для FFmpeg.
        """
        active_plugins = [p for p in self.plugins.values() if p.is_enabled()]

        if not active_plugins:
            return None, [], ["-map", "0:v", "-map", "0:a?"]

        filter_chains: List[str] = []
        extra_inputs: List[str] = []
        current_input_label = "0:v"

        for i, plugin in enumerate(active_plugins):
            is_last = i == len(active_plugins) - 1
            output_label = "outv" if is_last else f"v_step_{i}"

            try:
                filter_part, extra_inputs = plugin.build_filter(
                    input_label=current_input_label,
                    output_label=output_label,
                    extra_inputs=extra_inputs,
                )
                filter_chains.append(filter_part)
                current_input_label = output_label
            except Exception as e:
                logger.error(
                    f"Ошибка в build_filter плагина '{plugin.name}': {e}. Плагин пропущен."
                )

        extra_input_args: List[str] = []
        for inp in extra_inputs:
            extra_input_args.extend(["-i", inp])

        full_filter_complex = ";".join(filter_chains)
        map_args = ["-map", "[outv]", "-map", "0:a?"]

        return full_filter_complex, extra_input_args, map_args
