"""
Плагин наложения логотипа / водяного знака (PNG / JPG).
Поддерживает масштабирование, прозрачность и выбор позиции.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.config import BASE_DIR, ConfigManager, LogoPluginConfig, Position
from core.plugins.base import BaseOverlayPlugin

logger = logging.getLogger("stream_server.plugins.logo")


class LogoOverlayPlugin(BaseOverlayPlugin):
    """
    Плагин наложения графического логотипа поверх видеопотока.
    """

    def __init__(self, config_manager: ConfigManager):
        super().__init__(name="logo")
        self.config_manager = config_manager

    def _get_config(self) -> LogoPluginConfig:
        return self.config_manager.get_settings().plugins.logo

    def _resolve_image_path(self, raw_path: str) -> Path:
        """
        Разрешает путь к изображению логотипа:
        1. Если абсолютный и существует — возвращает его.
        2. Если существует относительно BASE_DIR (папки server/) — возвращает его.
        3. Если существует относительно текущей рабочей директории — возвращает его.
        4. Иначе возвращает (BASE_DIR / raw_path).resolve().
        """
        if not raw_path:
            return (BASE_DIR / "config" / "logo.png").resolve()
        p = Path(raw_path)
        if p.is_absolute() and p.exists():
            return p.resolve()
        if (BASE_DIR / p).exists():
            return (BASE_DIR / p).resolve()
        if p.exists():
            return p.resolve()
        return (BASE_DIR / p).resolve()

    def is_enabled(self) -> bool:
        cfg = self._get_config()
        if not cfg.enabled:
            return False
        logo_path = self._resolve_image_path(cfg.image_path)
        if not logo_path.exists() or not logo_path.is_file():
            logger.warning(
                f"Плагин Logo включен, но файл логотипа не найден по пути: {cfg.image_path} (разрешен: {logo_path})"
            )
            return False
        return True

    def get_settings_schema(self) -> Dict[str, Any]:
        cfg = self._get_config()
        return {
            "name": self.name,
            "title": "Водяной знак / Логотип",
            "enabled": cfg.enabled,
            "image_path": cfg.image_path,
            "position": cfg.position.value,
            "scale_width": cfg.scale_width,
            "opacity": cfg.opacity,
            "positions_available": [p.value for p in Position],
        }

    def build_filter(
        self,
        input_label: str,
        output_label: str,
        extra_inputs: List[str],
    ) -> Tuple[str, List[str]]:
        cfg = self._get_config()
        logo_path = self._resolve_image_path(cfg.image_path)

        # Добавляем путь к логотипу в список дополнительных входов FFmpeg
        new_extra_inputs = list(extra_inputs)
        new_extra_inputs.append(str(logo_path.resolve()))

        # Индекс добавленного входа (основное видео — 0, доп. входы начинаются с 1)
        input_idx = len(new_extra_inputs)
        logo_label = f"logo_in_{input_idx}"
        scaled_label = f"logo_scaled_{input_idx}"

        # Координаты наложения
        pos_map = {
            Position.TOP_LEFT: "x=20:y=20",
            Position.TOP_RIGHT: "x=main_w-overlay_w-20:y=20",
            Position.BOTTOM_LEFT: "x=20:y=main_h-overlay_h-20",
            Position.BOTTOM_RIGHT: "x=main_w-overlay_w-20:y=main_h-overlay_h-20",
            Position.CENTER: "x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2",
        }
        overlay_coords = pos_map.get(cfg.position, "x=main_w-overlay_w-20:y=20")

        # Цепочка: масштабирование -> прозрачность -> наложение
        # repeatlast=1 гарантирует удержание статической картинки на всем протяжении видео
        filter_parts = [
            f"[{input_idx}:v]scale={cfg.scale_width}:-1,format=rgba,colorchannelmixer=aa={cfg.opacity:.2f}[{scaled_label}]",
            f"[{input_label}][{scaled_label}]overlay={overlay_coords}:repeatlast=1[{output_label}]",
        ]

        return ";".join(filter_parts), new_extra_inputs
