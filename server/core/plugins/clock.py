"""
Плагин наложения системного времени с использованием FFmpeg фильтра drawtext.
Поддерживает форматы времени, позиционирование по углам, стилизацию шрифта и подложку.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import ClockFormat, ClockPluginConfig, ConfigManager
from core.plugins.base import BaseOverlayPlugin


def find_system_font() -> Optional[str]:
    """
    Кроссплатформенный поиск стандартного шрифта без засечек для drawtext.
    Предотвращает ошибки отсутствия fontconfig в Windows/Linux.
    """
    candidates = []
    if sys.platform.startswith("win"):
        candidates = [
            Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "arial.ttf",
            Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "segoeui.ttf",
            Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "tahoma.ttf",
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        ]

    for p in candidates:
        if p.exists():
            # Экранируем двоеточия для синтаксиса фильтра FFmpeg
            escaped = str(p.resolve()).replace("\\", "/").replace(":", "\\:")
            return escaped
    return None


class ClockOverlayPlugin(BaseOverlayPlugin):
    """
    Плагин отображения системного времени (часы) поверх трансляции.
    """

    def __init__(self, config_manager: ConfigManager):
        super().__init__(name="clock")
        self.config_manager = config_manager
        self._font_path = find_system_font()

    def _get_config(self) -> ClockPluginConfig:
        return self.config_manager.get_settings().plugins.clock

    def is_enabled(self) -> bool:
        return self._get_config().enabled

    def get_settings_schema(self) -> Dict[str, Any]:
        cfg = self._get_config()
        return {
            "name": self.name,
            "title": "Системные часы",
            "enabled": cfg.enabled,
            "position": cfg.position,
            "format": cfg.format.value,
            "font_size": cfg.font_size,
            "font_color": cfg.font_color,
            "box_enabled": cfg.box_enabled,
            "box_color": cfg.box_color,
            "formats_available": [f.value for f in ClockFormat],
            "positions_available": [
                "top_left",
                "top_right",
                "bottom_left",
                "bottom_right",
            ],
        }

    def build_filter(
        self,
        input_label: str,
        output_label: str,
        extra_inputs: List[str],
    ) -> Tuple[str, List[str]]:
        cfg = self._get_config()

        # Форматирование времени без проблемных двоеточий в strftime:
        # %T = HH:MM:SS, %R = HH:MM, %d.%m.%Y %T = DD.MM.YYYY HH:MM:SS
        format_mapping = {
            ClockFormat.TIME_FULL: "%T",
            ClockFormat.TIME_SHORT: "%R",
            ClockFormat.DATETIME_FULL: "%d.%m.%Y %T",
        }
        strftime_fmt = format_mapping.get(cfg.format, "%T")

        # Позиционирование по углам с отступами в 20px
        pos_map = {
            "top_left": "x=20:y=20",
            "top_right": "x=w-tw-20:y=20",
            "bottom_left": "x=20:y=h-th-20",
            "bottom_right": "x=w-tw-20:y=h-th-20",
        }
        coords = pos_map.get(cfg.position, "x=20:y=20")

        filter_args = [
            f"text='%{{localtime\\:{strftime_fmt}}}'",
            f"fontsize={cfg.font_size}",
            f"fontcolor={cfg.font_color}",
            coords,
        ]

        if self._font_path:
            filter_args.append(f"fontfile='{self._font_path}'")

        if cfg.box_enabled:
            filter_args.append(f"box=1:boxcolor={cfg.box_color}:boxborderw=6")
        else:
            filter_args.append("box=0")

        filter_expr = ":".join(filter_args)
        full_filter = f"[{input_label}]drawtext={filter_expr}[{output_label}]"

        return full_filter, extra_inputs
