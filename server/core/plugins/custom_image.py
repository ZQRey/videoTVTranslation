"""
Пользовательский плагин наложения дополнительного изображения или баннера (Custom Image / Banner).
Позволяет выводить спонсорские плашки, QR-коды или рекламные баннеры.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.config import ConfigManager
from core.plugins.base import BaseOverlayPlugin

logger = logging.getLogger("stream_server.plugins.custom_image")


class CustomImageOverlayPlugin(BaseOverlayPlugin):
    """
    Плагин пользовательского баннера / изображения.
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
        self.plugin_type = "image"

        self.defaults = {
            "enabled": False,
            "image_path": "",
            "position": "bottom_left",
            "scale_width": 140,
            "opacity": 0.9,
            "margin_x": 20,
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
        if not bool(cfg.get("enabled", False)):
            return False
        img_path = Path(str(cfg.get("image_path", "")))
        return img_path.exists() and img_path.is_file()

    def get_settings_schema(self) -> Dict[str, Any]:
        cfg = self._get_config()
        return {
            "name": self.name,
            "title": self.title,
            "is_custom": True,
            "plugin_type": self.plugin_type,
            "enabled": cfg.get("enabled", False),
            "image_path": cfg.get("image_path", ""),
            "position": cfg.get("position", "bottom_left"),
            "scale_width": cfg.get("scale_width", 140),
            "opacity": cfg.get("opacity", 0.9),
            "margin_x": cfg.get("margin_x", 20),
            "margin_y": cfg.get("margin_y", 20),
            "positions_available": [
                "bottom_left",
                "bottom_right",
                "top_left",
                "top_right",
                "center",
            ],
        }

    def build_filter(
        self,
        input_label: str,
        output_label: str,
        extra_inputs: List[str],
    ) -> Tuple[str, List[str]]:
        cfg = self._get_config()
        img_path = Path(str(cfg.get("image_path", ""))).resolve()

        new_extra_inputs = list(extra_inputs)
        new_extra_inputs.append(str(img_path))

        input_idx = len(new_extra_inputs)
        scaled_label = f"img_scaled_{input_idx}"

        pos = cfg.get("position", "bottom_left")
        mx = int(cfg.get("margin_x", 20))
        my = int(cfg.get("margin_y", 20))

        pos_map = {
            "top_left": f"x={mx}:y={my}",
            "top_right": f"x=main_w-overlay_w-{mx}:y={my}",
            "bottom_left": f"x={mx}:y=main_h-overlay_h-{my}",
            "bottom_right": f"x=main_w-overlay_w-{mx}:y=main_h-overlay_h-{my}",
            "center": "x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2",
        }
        overlay_coords = pos_map.get(pos, f"x={mx}:y=main_h-overlay_h-{my}")

        scale_width = int(cfg.get("scale_width", 140))
        opacity = float(cfg.get("opacity", 0.9))

        filter_parts = [
            f"[{input_idx}:v]scale={scale_width}:-1,format=rgba,colorchannelmixer=aa={opacity:.2f}[{scaled_label}]",
            f"[{input_label}][{scaled_label}]overlay={overlay_coords}:repeatlast=1[{output_label}]",
        ]

        return ";".join(filter_parts), new_extra_inputs
