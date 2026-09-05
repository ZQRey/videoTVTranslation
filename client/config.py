"""
Модуль конфигурации десктопного плеера.
Управляет загрузкой, валидацией и сохранением параметров подключения в client_config.json.
Использует исключительно стандартную библиотеку Python (dataclasses, json)
без внешних зависимостей.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("desktop_player.config")


@dataclass
class ClientConfig:
    """Модель конфигурации клиента."""
    server_host: str = ""
    rtsp_port: int = 8554
    stream_path: str = "live"
    network_caching: int = 1000
    api_port: int = 8000
    client_id: str = ""

    def __post_init__(self) -> None:
        """Валидация и очистка значений конфигурации."""
        if isinstance(self.server_host, str):
            self.server_host = self.server_host.strip()
        else:
            self.server_host = ""

        try:
            self.rtsp_port = int(self.rtsp_port)
            if not (1 <= self.rtsp_port <= 65535):
                self.rtsp_port = 8554
        except (ValueError, TypeError):
            self.rtsp_port = 8554

        if isinstance(self.stream_path, str):
            cleaned = self.stream_path.strip().lstrip("/")
            self.stream_path = cleaned if cleaned else "live"
        else:
            self.stream_path = "live"

        try:
            self.network_caching = int(self.network_caching)
            if not (0 <= self.network_caching <= 10000):
                self.network_caching = 1000
        except (ValueError, TypeError):
            self.network_caching = 1000

        try:
            self.api_port = int(self.api_port)
            if not (1 <= self.api_port <= 65535):
                self.api_port = 8000
        except (ValueError, TypeError):
            self.api_port = 8000

        if not isinstance(self.client_id, str) or not self.client_id.strip():
            try:
                host = socket.gethostname() or "client"
            except Exception:
                host = "client"
            self.client_id = f"{host}-{uuid.uuid4().hex[:6]}"
        else:
            self.client_id = self.client_id.strip()

    @property
    def rtsp_url(self) -> str:
        """Формирует полный URL-адрес RTSP потока."""
        clean_path = self.stream_path.lstrip("/")
        return f"rtsp://{self.server_host}:{self.rtsp_port}/{clean_path}"

    @property
    def api_url(self) -> str:
        """Формирует базовый HTTP URL сервера управления."""
        return f"http://{self.server_host}:{self.api_port}"

    @property
    def ws_client_url(self) -> str:
        """Формирует WebSocket URL для обмена телеметрией и командами управления."""
        return f"ws://{self.server_host}:{self.api_port}/ws/client"

    def is_configured(self) -> bool:
        """Возвращает True, если адрес сервера задан корректно."""
        return bool(self.server_host and self.server_host.strip())

    def model_dump(self) -> Dict[str, Any]:
        """Совместимость с Pydantic интерфейсом."""
        return asdict(self)

    def model_dump_json(self, indent: int = 2) -> str:
        """Сериализация в форматированный JSON."""
        return json.dumps(asdict(self), indent=indent, ensure_ascii=False)

    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> ClientConfig:
        """Создание экземпляра из словаря данных."""
        return cls(
            server_host=str(data.get("server_host", "")),
            rtsp_port=data.get("rtsp_port", 8554),
            stream_path=str(data.get("stream_path", "live")),
            network_caching=data.get("network_caching", 1000),
            api_port=data.get("api_port", 8000),
            client_id=str(data.get("client_id", "")),
        )


class ConfigManager:
    """Менеджер для загрузки и атомарного сохранения конфигурации."""

    DEFAULT_FILENAME = "client_config.json"

    def __init__(self, config_path: Optional[str | Path] = None) -> None:
        if config_path:
            self.config_path = Path(config_path).resolve()
        else:
            # По умолчанию рядом со скриптом клиента
            base_dir = Path(__file__).resolve().parent
            self.config_path = base_dir / self.DEFAULT_FILENAME

        self._current_config: ClientConfig = self.load()

    @property
    def config(self) -> ClientConfig:
        """Текущая активная конфигурация."""
        return self._current_config

    def load(self) -> ClientConfig:
        """
        Загружает конфигурацию из файла.
        Если файл отсутствует или поврежден, возвращает конфигурацию по умолчанию.
        """
        if not self.config_path.exists():
            logger.info("Файл конфигурации %s не найден. Создается конфигурация по умолчанию.", self.config_path)
            self._current_config = ClientConfig()
            self.save(self._current_config)
            return self._current_config

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    self._current_config = ClientConfig()
                    self.save(self._current_config)
                    return self._current_config
                data = json.loads(content)
                self._current_config = ClientConfig.model_validate(data)
                # Если client_id отсутствовал в старом конфиге, сохраняем сгенерированный
                if not data.get("client_id"):
                    self.save(self._current_config)
                logger.info("Конфигурация успешно загружена из %s (client_id=%s)", self.config_path, self._current_config.client_id)
                return self._current_config
        except Exception as err:
            logger.error("Ошибка при чтении файла конфигурации %s: %s. Используются значения по умолчанию.", self.config_path, err)
            self._current_config = ClientConfig()
            return self._current_config

    def save(self, config: ClientConfig) -> bool:
        """
        Атомарно сохраняет переданную конфигурацию на диск.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = None
        try:
            # Атомарная запись через временный файл в той же директории
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.config_path.parent),
                delete=False,
                suffix=".tmp"
            ) as f:
                temp_file = f.name
                json_str = config.model_dump_json(indent=2)
                f.write(json_str)
                f.flush()
                os.fsync(f.fileno())

            # Замена целевого файла
            os.replace(temp_file, str(self.config_path))
            self._current_config = config
            logger.info("Конфигурация успешно сохранена в %s", self.config_path)
            return True
        except Exception as err:
            logger.error("Не удалось сохранить конфигурацию в %s: %s", self.config_path, err)
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            return False

    def update(
        self,
        server_host: str,
        rtsp_port: int = 8554,
        stream_path: str = "live",
        network_caching: int = 300,
        api_port: int = 8000,
        client_id: Optional[str] = None,
    ) -> ClientConfig:
        """Обновляет поля конфигурации и сохраняет файл."""
        new_config = ClientConfig(
            server_host=server_host,
            rtsp_port=rtsp_port,
            stream_path=stream_path,
            network_caching=network_caching,
            api_port=api_port,
            client_id=client_id or self._current_config.client_id,
        )
        self.save(new_config)
        return new_config
