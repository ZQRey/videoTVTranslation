"""
Модуль конфигурации и управления настройками сервера.
Использует Pydantic v2 для строгой валидации и потокобезопасный ConfigManager
с атомарным сохранением настроек на диск.
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.crypto import encrypt_secret, is_encrypted

logger = logging.getLogger("stream_server.config")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH_DEFAULT = BASE_DIR / "config" / "settings.json"


class Position(str, Enum):
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    CENTER = "center"


class ClockFormat(str, Enum):
    TIME_FULL = "HH:mm:ss"
    TIME_SHORT = "HH:mm"
    DATETIME_FULL = "dd.MM.yyyy HH:mm:ss"


class LogoPluginConfig(BaseModel):
    enabled: bool = False
    image_path: str = "config/logo.png"
    position: Position = Position.TOP_RIGHT
    scale_width: int = Field(default=160, ge=10, le=3840)
    opacity: float = Field(default=0.85, ge=0.1, le=1.0)


class ClockPluginConfig(BaseModel):
    enabled: bool = False
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_left"
    format: ClockFormat = ClockFormat.TIME_FULL
    font_size: int = Field(default=28, ge=8, le=120)
    font_color: str = Field(default="white")
    box_enabled: bool = True
    box_color: str = Field(default="0x00000080")


class PipPluginConfig(BaseModel):
    enabled: bool = False
    stream_url: str = Field(default="")
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "bottom_right"
    width: int = Field(default=320, ge=64, le=1920)
    margin_x: int = Field(default=20, ge=0, le=500)
    margin_y: int = Field(default=20, ge=0, le=500)


class PluginsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    logo: LogoPluginConfig = Field(default_factory=LogoPluginConfig)
    clock: ClockPluginConfig = Field(default_factory=ClockPluginConfig)
    pip: PipPluginConfig = Field(default_factory=PipPluginConfig)


class CircuitBreakerConfig(BaseModel):
    max_consecutive_errors: int = Field(default=10, ge=1, le=100)
    healthy_playback_threshold_sec: float = Field(default=15.0, ge=1.0, le=300.0)


class LocalAuthConfig(BaseModel):
    enabled: bool = True
    username: str = "admin"
    password_hash: str = ""


class DomainAuthConfig(BaseModel):
    enabled: bool = True
    server: str = "dc01.gp1.loc"
    port: int = 389
    use_ssl: bool = False
    domain: str = "gp1.loc"
    base_dn: str = "DC=gp1,DC=loc"
    service_user: str = "tvuser"
    service_password: str = ""
    admin_group: str = "Администраторы домена"


class AuthConfig(BaseModel):
    session_lifetime_hours: int = Field(default=24, ge=1, le=720)
    local: LocalAuthConfig = Field(default_factory=LocalAuthConfig)
    domain: DomainAuthConfig = Field(default_factory=DomainAuthConfig)


class ScheduleConfig(BaseModel):
    mode: Literal["24/7", "interval"] = "24/7"
    start_time: str = Field(default="08:00")
    end_time: str = Field(default="20:00")
    days_of_week: List[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])
    action_off: Literal["standby", "adb_sleep"] = "standby"


class ServerSettings(BaseModel):
    media_dir: str = Field(default="media")
    scan_interval: int = Field(default=10, ge=1, le=3600)
    rtsp_target_url: str = Field(default="rtsp://localhost:8554/live")
    mediamtx_hls_url: str = Field(default="http://localhost:8888/live")
    web_host: str = Field(default="0.0.0.0")
    web_port: int = Field(default=8000, ge=1, le=65535)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    custom_plugins_meta: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    @field_validator("media_dir")
    @classmethod
    def validate_media_dir(cls, v: str) -> str:
        # Нормализация пути для кроссплатформенности
        path = Path(v.strip())
        return str(path)


class ConfigManager:
    """
    Потокобезопасный менеджер конфигурации.
    Обеспечивает чтение, валидацию и атомарную запись JSON на диск.
    """

    def __init__(self, config_path: Path = CONFIG_PATH_DEFAULT):
        self.config_path = Path(config_path)
        self._lock = asyncio.Lock()
        self._settings: ServerSettings = self._load_initial_settings()

    def _load_initial_settings(self) -> ServerSettings:
        """Загрузка настроек с диска при старте приложения с fallback на значения по умолчанию."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Автомиграция: если пароль AD хранится в открытом виде, шифруем его
                domain_cfg = data.get("auth", {}).get("domain", {})
                raw_pwd = domain_cfg.get("service_password")
                if raw_pwd and not is_encrypted(raw_pwd):
                    domain_cfg["service_password"] = encrypt_secret(raw_pwd)
                    data["auth"]["domain"] = domain_cfg
                    settings = ServerSettings.model_validate(data)
                    self._save_to_disk_sync(settings)
                    logger.info("Пароль Active Directory автоматически зашифрован и сохранен на диск.")
                    return self._apply_env_overrides(settings)

                settings = ServerSettings.model_validate(data)
                logger.info(f"Конфигурация успешно загружена из {self.config_path}")
                return self._apply_env_overrides(settings)
            except Exception as e:
                logger.warning(
                    f"Ошибка чтения конфигурации из {self.config_path}: {e}. Используются настройки по умолчанию."
                )

        # Если файл не существует, создаем его со значениями по умолчанию
        default_settings = ServerSettings()
        default_settings = self._apply_env_overrides(default_settings)
        self._save_to_disk_sync(default_settings)
        return default_settings

    def _apply_env_overrides(self, settings: ServerSettings) -> ServerSettings:
        """Применение переменных окружения (актуально для контейнеров Docker)."""
        data = settings.model_dump()
        if "MEDIA_DIR" in os.environ:
            data["media_dir"] = os.environ["MEDIA_DIR"]
        if "RTSP_TARGET_URL" in os.environ:
            data["rtsp_target_url"] = os.environ["RTSP_TARGET_URL"]
        if "MEDIAMTX_HLS_URL" in os.environ:
            data["mediamtx_hls_url"] = os.environ["MEDIAMTX_HLS_URL"]
        if "WEB_PORT" in os.environ:
            try:
                data["web_port"] = int(os.environ["WEB_PORT"])
            except ValueError:
                pass
        return ServerSettings.model_validate(data)

    def _save_to_disk_sync(self, settings: ServerSettings) -> None:
        """Синхронное атомарное сохранение настроек на диск."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        dump_data = settings.model_dump_json(indent=2)
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(self.config_path.parent),
                delete=False,
                encoding="utf-8",
            ) as tf:
                tf.write(dump_data)
                temp_file = tf.name
            shutil.move(temp_file, str(self.config_path))
            logger.info(f"Конфигурация успешно сохранена в {self.config_path}")
        except Exception as e:
            logger.error(f"Не удалось сохранить конфигурацию: {e}")
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            raise

    def get_settings(self) -> ServerSettings:
        """Получить текущий снимок настроек."""
        return self._settings.model_copy(deep=True)

    def get_public_settings(self) -> dict:
        """Получить словарь настроек без секретных данных (паролей)."""
        data = self._settings.model_dump()
        if "auth" in data:
            if "local" in data["auth"]:
                data["auth"]["local"]["password_hash"] = "******" if data["auth"]["local"].get("password_hash") else ""
            if "domain" in data["auth"]:
                has_pwd = bool(data["auth"]["domain"].get("service_password"))
                data["auth"]["domain"]["has_service_password"] = has_pwd
                data["auth"]["domain"]["service_password"] = "******" if has_pwd else ""
        return data

    async def update_settings(self, new_data: dict) -> ServerSettings:
        """Асинхронное обновление настроек с валидацией и сохранением."""
        async with self._lock:
            # Слияние текущих данных с новыми
            current_dict = self._settings.model_dump()
            merged_dict = self._deep_update(current_dict, new_data)
            validated = ServerSettings.model_validate(merged_dict)
            self._settings = validated
            await asyncio.to_thread(self._save_to_disk_sync, validated)
            return self._settings.model_copy(deep=True)

    async def remove_custom_plugin(self, name: str) -> ServerSettings:
        """Потокобезопасное полное удаление кастомного плагина из настроек."""
        async with self._lock:
            current_dict = self._settings.model_dump()
            if (
                "custom_plugins_meta" in current_dict
                and name in current_dict["custom_plugins_meta"]
            ):
                del current_dict["custom_plugins_meta"][name]
            if "plugins" in current_dict and name in current_dict["plugins"]:
                del current_dict["plugins"][name]
            validated = ServerSettings.model_validate(current_dict)
            self._settings = validated
            await asyncio.to_thread(self._save_to_disk_sync, validated)
            return self._settings.model_copy(deep=True)

    def _deep_update(self, base_dict: dict, update_dict: dict) -> dict:
        """Рекурсивное обновление словарей с защитой от перезаписи масок '******' и шифрованием паролей."""
        for key, value in update_dict.items():
            # Если передана маска пароля, не затираем реальный пароль
            if value == "******" and key in ("service_password", "password_hash"):
                continue
            # Если передан новый пароль service_password - шифруем его
            if key == "service_password":
                if value == "":
                    continue
                if not is_encrypted(value):
                    base_dict[key] = encrypt_secret(value)
                else:
                    base_dict[key] = value
                continue
            if (
                isinstance(value, dict)
                and key in base_dict
                and isinstance(base_dict[key], dict)
            ):
                base_dict[key] = self._deep_update(base_dict[key], value)
            else:
                base_dict[key] = value
        return base_dict


# Глобальный экземпляр менеджера конфигурации
config_manager = ConfigManager()
