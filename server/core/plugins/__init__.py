"""
Пакет плагинов оверлеев для транслятора видеопотока.
"""

from core.plugins.base import BaseOverlayPlugin
from core.plugins.clock import ClockOverlayPlugin
from core.plugins.logo import LogoOverlayPlugin
from core.plugins.manager import PluginManager
from core.plugins.pip import PipOverlayPlugin

__all__ = [
    "BaseOverlayPlugin",
    "LogoOverlayPlugin",
    "ClockOverlayPlugin",
    "PipOverlayPlugin",
    "PluginManager",
]
