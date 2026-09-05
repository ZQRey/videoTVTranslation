"""
Базовый абстрактный класс для всех плагинов видео-оверлеев.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple


class BaseOverlayPlugin(ABC):
    """
    Абстрактный класс плагина наложения графики/текста/врезки в FFmpeg.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def is_enabled(self) -> bool:
        """Возвращает True, если плагин активирован в конфигурации."""
        pass

    @abstractmethod
    def get_settings_schema(self) -> Dict[str, Any]:
        """Возвращает текущие настройки и JSON-схему настроек плагина."""
        pass

    @abstractmethod
    def build_filter(
        self,
        input_label: str,
        output_label: str,
        extra_inputs: List[str],
    ) -> Tuple[str, List[str]]:
        """
        Формирует фрагмент фильтра для -filter_complex.

        :param input_label: имя входного видеопотока (напр., "0:v" или "v1")
        :param output_label: имя выходного видеопотока (напр., "v2" или "outv")
        :param extra_inputs: список дополнительных источников ввода (-i ...),
                             добавленных предыдущими плагинами.
        :return: кортеж (строка_фильтра, обновленный_список_extra_inputs)
        """
        pass
