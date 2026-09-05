"""
Пользовательский плагин для применения произвольных видеофильтров и эффектов FFmpeg.
Позволяет использовать цветокоррекцию, фильтры резкости, размытия, виньетки и др.
"""

import logging
from typing import Any, Dict, List, Tuple

from core.config import ConfigManager
from core.plugins.base import BaseOverlayPlugin

logger = logging.getLogger("stream_server.plugins.custom_filter")


class CustomFilterOverlayPlugin(BaseOverlayPlugin):
    """
    Плагин произвольного FFmpeg видеофильтра.
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
        self.plugin_type = "filter"

        self.defaults = {
            "enabled": False,
            "filter_expr": "eq=brightness=0.04:contrast=1.1:saturation=1.15",
            "preset": "color_boost",
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
        return bool(cfg.get("enabled", False)) and bool(
            str(cfg.get("filter_expr", "")).strip()
        )

    def get_settings_schema(self) -> Dict[str, Any]:
        cfg = self._get_config()
        return {
            "name": self.name,
            "title": self.title,
            "is_custom": True,
            "plugin_type": self.plugin_type,
            "enabled": cfg.get("enabled", False),
            "filter_expr": cfg.get("filter_expr", ""),
            "preset": cfg.get("preset", "custom"),
            "presets_available": [
                {
                    "id": "color_boost",
                    "title": "Насыщенность и контраст",
                    "expr": "eq=brightness=0.03:contrast=1.12:saturation=1.2",
                },
                {
                    "id": "warm_vintage",
                    "title": "Теплый винтаж",
                    "expr": "curves=vintage",
                },
                {
                    "id": "vignette",
                    "title": "Кинематографичная виньетка",
                    "expr": "vignette=PI/4",
                },
                {
                    "id": "soft_blur",
                    "title": "Легкое размытие (Blur)",
                    "expr": "boxblur=2:1",
                },
                {
                    "id": "custom",
                    "title": "Пользовательское выражение",
                    "expr": cfg.get("filter_expr", ""),
                },
            ],
        }

    def build_filter(
        self,
        input_label: str,
        output_label: str,
        extra_inputs: List[str],
    ) -> Tuple[str, List[str]]:
        cfg = self._get_config()
        filter_expr = cfg.get("filter_expr", "").strip()

        # Формирование фильтра в цепочке
        full_filter = f"[{input_label}]{filter_expr}[{output_label}]"
        return full_filter, extra_inputs
