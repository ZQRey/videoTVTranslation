"""
Плагин врезки стороннего видеопотока "Картинка в картинке" (Picture-in-Picture).
Позволяет накладывать live RTSP/HTTP поток поверх основного эфира.
"""

import logging
from typing import Any, Dict, List, Tuple

from core.config import ConfigManager, PipPluginConfig
from core.plugins.base import BaseOverlayPlugin

logger = logging.getLogger("stream_server.plugins.pip")


class PipOverlayPlugin(BaseOverlayPlugin):
    """
    Плагин PiP для врезки внешнего RTSP/HTTP стрима.
    """

    def __init__(self, config_manager: ConfigManager):
        super().__init__(name="pip")
        self.config_manager = config_manager

    def _get_config(self) -> PipPluginConfig:
        return self.config_manager.get_settings().plugins.pip

    def is_enabled(self) -> bool:
        cfg = self._get_config()
        if not cfg.enabled:
            return False
        if not cfg.stream_url or not cfg.stream_url.strip():
            logger.warning("Плагин PiP включен, но URL потока (stream_url) не задан.")
            return False
        return True

    def get_settings_schema(self) -> Dict[str, Any]:
        cfg = self._get_config()
        return {
            "name": self.name,
            "title": "Картинка в картинке (PiP)",
            "enabled": cfg.enabled,
            "stream_url": cfg.stream_url,
            "position": cfg.position,
            "width": cfg.width,
            "margin_x": cfg.margin_x,
            "margin_y": cfg.margin_y,
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
        new_extra_inputs = list(extra_inputs)
        new_extra_inputs.append(cfg.stream_url.strip())

        pip_idx = len(new_extra_inputs)
        scaled_label = f"pip_scaled_{pip_idx}"

        pos_map = {
            "top_left": f"x={cfg.margin_x}:y={cfg.margin_y}",
            "top_right": f"x=main_w-overlay_w-{cfg.margin_x}:y={cfg.margin_y}",
            "bottom_left": f"x={cfg.margin_x}:y=main_h-overlay_h-{cfg.margin_y}",
            "bottom_right": f"x=main_w-overlay_w-{cfg.margin_x}:y=main_h-overlay_h-{cfg.margin_y}",
        }
        overlay_coords = pos_map.get(
            cfg.position,
            f"x=main_w-overlay_w-{cfg.margin_x}:y=main_h-overlay_h-{cfg.margin_y}",
        )

        filter_parts = [
            f"[{pip_idx}:v]scale={cfg.width}:-1[scaled_label]",
            f"[{input_label}][{scaled_label}]overlay={overlay_coords}[{output_label}]",
        ]

        # Замена имени метки для уникальности
        filter_str = (
            f"[{pip_idx}:v]scale={cfg.width}:-1[{scaled_label}];"
            f"[{input_label}][{scaled_label}]overlay={overlay_coords}[{output_label}]"
        )

        return filter_str, new_extra_inputs
