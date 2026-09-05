"""
Плагин наложения текста и бегущей строки (Text & Ticker Overlay).
Поддерживает статический текст и плавную бесконечную анимацию прокрутки.
"""

import logging
from typing import Any, Dict, List, Tuple

from core.config import ConfigManager
from core.plugins.base import BaseOverlayPlugin
from core.plugins.clock import find_system_font

logger = logging.getLogger("stream_server.plugins.custom_text")


class TextTickerOverlayPlugin(BaseOverlayPlugin):
    """
    Пользовательский плагин бегущей строки или статической текстовой плашки.
    """

    def __init__(
        self,
        name: str,
        title: str,
        config_manager: ConfigManager,
        default_config: Dict[str, Any] | None = None,
    ):
        super().__init__(name=name)
        self.title = title
        self.config_manager = config_manager
        self.is_custom = True
        self.plugin_type = "text_ticker"
        self._font_path = find_system_font()

        # Значения по умолчанию
        self.defaults = {
            "enabled": False,
            "text": "Эфир телеканала • Прямая трансляция",
            "mode": "scroll",  # 'scroll' | 'static'
            "speed": 120,  # px/sec
            "position": "bottom",  # 'bottom', 'top', 'center'
            "font_size": 24,
            "font_color": "white",
            "box_enabled": True,
            "box_color": "0x00000099",
            "margin_y": 20,
        }
        if default_config:
            self.defaults.update(default_config)

    def _get_config(self) -> Dict[str, Any]:
        settings = self.config_manager.get_settings()
        plugins_dict = settings.plugins.model_dump()
        cfg = plugins_dict.get(self.name, {})
        merged = dict(self.defaults)
        merged.update(cfg)
        return merged

    def is_enabled(self) -> bool:
        cfg = self._get_config()
        return bool(cfg.get("enabled", False)) and bool(str(cfg.get("text", "")).strip())

    def get_settings_schema(self) -> Dict[str, Any]:
        cfg = self._get_config()
        return {
            "name": self.name,
            "title": self.title,
            "is_custom": True,
            "plugin_type": self.plugin_type,
            "enabled": cfg.get("enabled", False),
            "text": cfg.get("text", ""),
            "mode": cfg.get("mode", "scroll"),
            "speed": cfg.get("speed", 120),
            "position": cfg.get("position", "bottom"),
            "font_size": cfg.get("font_size", 24),
            "font_color": cfg.get("font_color", "white"),
            "box_enabled": cfg.get("box_enabled", True),
            "box_color": cfg.get("box_color", "0x00000099"),
            "margin_y": cfg.get("margin_y", 20),
            "modes_available": ["scroll", "static"],
            "positions_available": ["bottom", "top", "center"],
        }

    def build_filter(
        self,
        input_label: str,
        output_label: str,
        extra_inputs: List[str],
    ) -> Tuple[str, List[str]]:
        cfg = self._get_config()
        text = cfg.get("text", "").replace("'", "\\'").replace(":", "\\:")
        font_size = int(cfg.get("font_size", 24))
        font_color = str(cfg.get("font_color", "white"))
        mode = cfg.get("mode", "scroll")
        speed = int(cfg.get("speed", 120))
        position = cfg.get("position", "bottom")
        margin_y = int(cfg.get("margin_y", 20))

        # Координаты Y
        if position == "top":
            y_coord = f"y={margin_y}"
        elif position == "center":
            y_coord = "y=(h-th)/2"
        else:  # bottom
            y_coord = f"y=h-th-{margin_y}"

        # Координаты X
        if mode == "scroll":
            # Зацикленное смещение справа налево
            x_coord = f"x=w-mod(t*{speed}\\,w+tw)"
        else:
            # По центру экрана по горизонтали
            x_coord = "x=(w-tw)/2"

        filter_args = [
            f"text='{text}'",
            f"fontsize={font_size}",
            f"fontcolor={font_color}",
            x_coord,
            y_coord,
        ]

        if self._font_path:
            filter_args.append(f"fontfile='{self._font_path}'")

        if cfg.get("box_enabled", True):
            box_col = cfg.get("box_color", "0x00000099")
            filter_args.append(f"box=1:boxcolor={box_col}:boxborderw=6")
        else:
            filter_args.append("box=0")

        filter_expr = ":".join(filter_args)
        full_filter = f"[{input_label}]drawtext={filter_expr}[{output_label}]"

        return full_filter, extra_inputs
