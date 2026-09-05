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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    token: str = ""
    schedule_mode: str = "global"
    schedule_start: str = "08:00"
    schedule_end: str = "20:00"
    schedule_days: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])

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

        # Синхронизация client_id и постоянного токена
        if not isinstance(self.token, str) or not self.token.strip():
            if isinstance(self.client_id, str) and self.client_id.strip():
                self.token = self.client_id.strip()
        else:
            self.token = self.token.strip()

        if not isinstance(self.client_id, str) or not self.client_id.strip():
            if self.token:
                self.client_id = self.token
            else:
                try:
                    host = socket.gethostname() or "client"
                except Exception:
                    host = "client"
                self.client_id = f"{host}-{uuid.uuid4().hex[:12]}"
                self.token = self.client_id
        else:
            self.client_id = self.client_id.strip()

        if not self.token:
            self.token = self.client_id

        if str(self.schedule_mode).lower() not in ("global", "24/7", "interval"):
            self.schedule_mode = "global"

        if not isinstance(self.schedule_start, str) or ":" not in self.schedule_start:
            self.schedule_start = "08:00"

        if not isinstance(self.schedule_end, str) or ":" not in self.schedule_end:
            self.schedule_end = "20:00"

        if not isinstance(self.schedule_days, list) or not self.schedule_days:
            self.schedule_days = [1, 2, 3, 4, 5, 6, 7]
        else:
            self.schedule_days = [int(d) for d in self.schedule_days if str(d).isdigit() and 1 <= int(d) <= 7]
            if not self.schedule_days:
                self.schedule_days = [1, 2, 3, 4, 5, 6, 7]

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
        cid = str(data.get("client_id", "")).strip()
        tok = str(data.get("token", "")).strip() or cid
        days = data.get("schedule_days")
        if days is None or not isinstance(days, list):
            days = [1, 2, 3, 4, 5, 6, 7]
        return cls(
            server_host=str(data.get("server_host", "")),
            rtsp_port=data.get("rtsp_port", 8554),
            stream_path=str(data.get("stream_path", "live")),
            network_caching=data.get("network_caching", 1000),
            api_port=data.get("api_port", 8000),
            client_id=cid or tok,
            token=tok or cid,
            schedule_mode=str(data.get("schedule_mode", "global")),
            schedule_start=str(data.get("schedule_start", "08:00")),
            schedule_end=str(data.get("schedule_end", "20:00")),
            schedule_days=[int(d) for d in days if str(d).isdigit()],
        )


def get_or_create_persistent_token(config_dir: Path) -> str:
    """
    Получает или создает постоянный уникальный токен клиента.
    Токен сохраняется в файле client_token.txt рядом с конфигурацией.
    """
    token_file = config_dir / "client_token.txt"
    if token_file.exists():
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                saved = f.read().strip()
                if saved:
                    return saved
        except Exception as e:
            logger.debug("Не удалось прочитать client_token.txt: %s", e)

    try:
        host = socket.gethostname() or "client"
    except Exception:
        host = "client"
    new_token = f"{host}-{uuid.uuid4().hex[:12]}"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(new_token)
    except Exception as e:
        logger.debug("Не удалось записать client_token.txt: %s", e)
    return new_token


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
        Всегда сохраняет и использует постоянный токен устройства.
        """
        base_dir = self.config_path.parent
        persistent_token = get_or_create_persistent_token(base_dir)

        if not self.config_path.exists():
            logger.info("Файл конфигурации %s не найден. Создается конфигурация по умолчанию.", self.config_path)
            self._current_config = ClientConfig(client_id=persistent_token, token=persistent_token)
            self.save(self._current_config)
            return self._current_config

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    self._current_config = ClientConfig(client_id=persistent_token, token=persistent_token)
                    self.save(self._current_config)
                    return self._current_config
                data = json.loads(content)
                if not data.get("client_id") and not data.get("token"):
                    data["client_id"] = persistent_token
                    data["token"] = persistent_token
                elif not data.get("token"):
                    data["token"] = data.get("client_id") or persistent_token
                elif not data.get("client_id"):
                    data["client_id"] = data.get("token") or persistent_token

                self._current_config = ClientConfig.model_validate(data)
                # Всегда сохраняем обновленную конфигурацию с токеном
                self.save(self._current_config)
                logger.info("Конфигурация успешно загружена из %s (client_id=%s)", self.config_path, self._current_config.client_id)
                return self._current_config
        except Exception as err:
            logger.error("Ошибка при чтении файла конфигурации %s: %s. Используются значения по умолчанию.", self.config_path, err)
            self._current_config = ClientConfig(client_id=persistent_token, token=persistent_token)
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
        token: Optional[str] = None,
        schedule_mode: Optional[str] = None,
        schedule_start: Optional[str] = None,
        schedule_end: Optional[str] = None,
        schedule_days: Optional[List[int]] = None,
    ) -> ClientConfig:
        """Обновляет поля конфигурации и сохраняет файл."""
        effective_cid = client_id or self._current_config.client_id
        effective_tok = token or self._current_config.token or effective_cid
        effective_mode = schedule_mode if schedule_mode is not None else self._current_config.schedule_mode
        effective_start = schedule_start if schedule_start is not None else self._current_config.schedule_start
        effective_end = schedule_end if schedule_end is not None else self._current_config.schedule_end
        effective_days = schedule_days if schedule_days is not None else self._current_config.schedule_days
        new_config = ClientConfig(
            server_host=server_host,
            rtsp_port=rtsp_port,
            stream_path=stream_path,
            network_caching=network_caching,
            api_port=api_port,
            client_id=effective_cid,
            token=effective_tok,
            schedule_mode=effective_mode,
            schedule_start=effective_start,
            schedule_end=effective_end,
            schedule_days=effective_days,
        )
        self.save(new_config)
        return new_config
