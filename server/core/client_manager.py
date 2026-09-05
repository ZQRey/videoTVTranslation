"""
Менеджер подключенных клиентских устройств вещания.
Отслеживает активные десктопные плееры, их экраны, IP-адреса и сетевые сессии,
а также осуществляет централизованное управление звуком, разрешением вещания,
режимом ожидания (standby) и удаленным выключением.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import WebSocket

logger = logging.getLogger("stream_server.clients")


class ClientDevice:
    """Модель состояния подключенного клиентского устройства."""

    def __init__(
        self,
        client_id: str,
        ip: str,
        hostname: str,
        os_info: str,
        screens: List[str],
        primary_screen: str,
        custom_name: str = "",
        audio_enabled: bool = True,
        stream_allowed: bool = True,
        standby: bool = False,
        registered_at: Optional[str] = None,
        connected_at: Optional[str] = None,
        last_seen: Optional[float] = None,
        websocket: Optional[WebSocket] = None,
    ):
        self.client_id = client_id
        self.ip = ip
        self.hostname = hostname
        self.custom_name = custom_name or hostname
        self.os_info = os_info
        self.screens = screens
        self.primary_screen = primary_screen
        self.audio_enabled = audio_enabled
        self.stream_allowed = stream_allowed
        self.standby = standby
        self.websocket = websocket
        self.registered_at = registered_at or time.strftime("%Y-%m-%d %H:%M:%S")
        self.connected_at = connected_at or time.strftime("%Y-%m-%d %H:%M:%S")
        self.last_seen = last_seen if last_seen is not None else time.time()

    @property
    def is_online(self) -> bool:
        """Клиент онлайн, если есть активный websocket и был пинг за последние 20 секунд."""
        return self.websocket is not None and (time.time() - self.last_seen) < 20

    @property
    def os_family(self) -> str:
        """
        Определение семейства операционной системы:
        'windows', 'linux', 'android', 'macos', 'unknown'.
        """
        raw = f"{self.os_info} {self.hostname}".lower()
        if "android" in raw:
            return "android"
        if "windows" in raw or "win32" in raw or "win64" in raw or "win10" in raw or "win11" in raw:
            return "windows"
        if any(dist in raw for dist in ("linux", "ubuntu", "debian", "fedora", "centos", "arch", "alpine", "redhat", "suse", "mint", "manjaro")):
            return "linux"
        if "darwin" in raw or "mac" in raw or "ios" in raw or "apple" in raw:
            return "macos"
        return "unknown"

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь для передачи через REST API и WebSocket."""
        return {
            "client_id": self.client_id,
            "custom_name": self.custom_name,
            "ip": self.ip,
            "hostname": self.hostname,
            "os_info": self.os_info,
            "os_family": self.os_family,
            "screens": self.screens,
            "primary_screen": self.primary_screen,
            "audio_enabled": self.audio_enabled,
            "stream_allowed": self.stream_allowed,
            "standby": self.standby,
            "registered_at": self.registered_at,
            "connected_at": self.connected_at,
            "last_seen_seconds_ago": max(0, int(time.time() - self.last_seen)) if self.last_seen > 0 else -1,
            "is_online": self.is_online,
        }

    def to_storage_dict(self) -> Dict[str, Any]:
        """Сериализация для персистентного хранения в JSON-файле."""
        return {
            "client_id": self.client_id,
            "custom_name": self.custom_name,
            "ip": self.ip,
            "hostname": self.hostname,
            "os_info": self.os_info,
            "os_family": self.os_family,
            "screens": self.screens,
            "primary_screen": self.primary_screen,
            "audio_enabled": self.audio_enabled,
            "stream_allowed": self.stream_allowed,
            "standby": self.standby,
            "registered_at": self.registered_at,
            "last_seen": self.last_seen,
        }


class ClientManager:
    """
    Потокобезопасный менеджер подключенных клиентских плееров с поддержкой
    персистентного хранения устройств в JSON.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self._clients: Dict[str, ClientDevice] = {}
        self._global_audio_enabled: bool = True
        self._lock = asyncio.Lock()
        self._storage_path = (
            storage_path
            if storage_path is not None
            else Path(__file__).resolve().parent.parent / "config" / "clients.json"
        )
        self._load_persisted_clients()

    def _load_persisted_clients(self) -> None:
        """Загрузка сохраненных клиентов из JSON-файла при старте."""
        if not self._storage_path or not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    cid = item.get("client_id")
                    if not cid:
                        continue
                    client = ClientDevice(
                        client_id=cid,
                        ip=item.get("ip", ""),
                        hostname=item.get("hostname", ""),
                        custom_name=item.get("custom_name", ""),
                        os_info=item.get("os_info", ""),
                        screens=item.get("screens", []),
                        primary_screen=item.get("primary_screen", ""),
                        audio_enabled=item.get("audio_enabled", True),
                        stream_allowed=item.get("stream_allowed", True),
                        standby=item.get("standby", False),
                        registered_at=item.get("registered_at"),
                        connected_at=None,
                        last_seen=0.0,
                        websocket=None,
                    )
                    self._clients[cid] = client
                logger.info("Загружено сохраненных клиентов: %d", len(self._clients))
        except Exception as e:
            logger.error("Ошибка при загрузке clients.json: %s", e)

    def _save_persisted_clients(self) -> None:
        """Сохранение состояния клиентов в JSON-файл."""
        if not self._storage_path:
            return
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = [c.to_storage_dict() for c in self._clients.values()]
            temp_file = self._storage_path.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_file.replace(self._storage_path)
        except Exception as e:
            logger.error("Ошибка при сохранении clients.json: %s", e)

    async def register_or_update(
        self,
        client_id: str,
        ip: str,
        data: Dict[str, Any],
        websocket: Optional[WebSocket] = None,
    ) -> ClientDevice:
        """Регистрация нового клиента или обновление информации существующего."""
        async with self._lock:
            hostname = data.get("hostname", "Unknown-Host")
            os_info = data.get("os_info", "Unknown-OS")
            screens = data.get("screens", [])
            primary_screen = data.get("primary_screen", "")

            if client_id in self._clients:
                client = self._clients[client_id]
                client.ip = ip
                client.hostname = hostname
                client.os_info = os_info
                client.screens = screens
                client.primary_screen = primary_screen
                client.last_seen = time.time()
                client.connected_at = time.strftime("%Y-%m-%d %H:%M:%S")
                if websocket is not None:
                    client.websocket = websocket
                logger.info("Обновлена телеметрия клиента [%s] (%s, %s)", client_id, hostname, ip)
            else:
                default_audio = self._global_audio_enabled
                audio_enabled = data.get("audio_enabled", default_audio)
                client = ClientDevice(
                    client_id=client_id,
                    ip=ip,
                    hostname=hostname,
                    custom_name=data.get("custom_name") or hostname,
                    os_info=os_info,
                    screens=screens,
                    primary_screen=primary_screen,
                    audio_enabled=audio_enabled and self._global_audio_enabled,
                    stream_allowed=data.get("stream_allowed", True),
                    standby=data.get("standby", False),
                    websocket=websocket,
                )
                self._clients[client_id] = client
                logger.info(
                    "Подключен новый клиент вещания [%s]: хост '%s', IP %s, экранов: %d",
                    client_id,
                    hostname,
                    ip,
                    len(screens),
                )

            self._save_persisted_clients()

            # Отправляем текущее состояние клиенту при подключении через WS
            if websocket is not None:
                try:
                    await websocket.send_json({
                        "type": "init_state",
                        "audio_enabled": client.audio_enabled,
                        "stream_allowed": client.stream_allowed,
                        "standby": client.standby,
                    })
                except Exception as e:
                    logger.debug("Не удалось отправить init_state клиенту [%s]: %s", client_id, e)

            return client

    async def unregister(self, client_id: str) -> None:
        """Отключение клиента при разрыве соединения (переход в офлайн)."""
        async with self._lock:
            client = self._clients.get(client_id)
            if client:
                client.websocket = None
                client.last_seen = 0.0
                logger.info("Клиент вещания [%s] (%s) отключился (перешел в офлайн).", client_id, client.hostname)
                self._save_persisted_clients()

    async def delete_client(self, client_id: str) -> bool:
        """Полное удаление сохраненного клиента из базы."""
        async with self._lock:
            client = self._clients.pop(client_id, None)
            if client:
                if client.websocket:
                    try:
                        await client.websocket.close()
                    except Exception:
                        pass
                self._save_persisted_clients()
                logger.info("Клиент [%s] удален из сохраненных.", client_id)
                return True
            return False

    def get_client(self, client_id: str) -> Optional[ClientDevice]:
        """Получение клиентского устройства по ID."""
        return self._clients.get(client_id)

    async def add_manual_client(
        self,
        ip: str,
        custom_name: str,
        os_info: Optional[str] = "Android",
        screens: Optional[List[str]] = None,
    ) -> ClientDevice:
        """
        Ручное добавление устройства в реестр (например, ТВ-приставки или Android-ТВ).
        """
        async with self._lock:
            # Если клиент с таким IP уже есть, обновляем его
            existing = next((c for c in self._clients.values() if c.ip == ip), None)
            if existing:
                if custom_name:
                    existing.custom_name = custom_name.strip()
                if os_info:
                    existing.os_info = os_info.strip()
                self._save_persisted_clients()
                return existing

            import uuid
            clean_ip = ip.replace(".", "-").replace(":", "-")
            cid = f"manual-{clean_ip}-{uuid.uuid4().hex[:6]}"
            hostname = custom_name or f"Device-{ip}"
            client = ClientDevice(
                client_id=cid,
                ip=ip,
                hostname=hostname,
                custom_name=custom_name or hostname,
                os_info=os_info or "Android",
                screens=screens or ["Primary (TV)"],
                primary_screen="Primary (TV)",
                audio_enabled=self._global_audio_enabled,
                stream_allowed=True,
                standby=False,
                registered_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                connected_at=None,
                last_seen=0.0,
                websocket=None,
            )
            self._clients[cid] = client
            self._save_persisted_clients()
            logger.info("Вручную добавлено устройство [%s] (%s, IP %s, ОС: %s)", cid, custom_name, ip, os_info)
            return client

    async def update_client_meta(
        self,
        client_id: str,
        custom_name: Optional[str] = None,
        os_info: Optional[str] = None,
        ip: Optional[str] = None,
    ) -> bool:
        """Обновление пользовательского имени, информации об ОС и/или IP клиента."""
        async with self._lock:
            client = self._clients.get(client_id)
            if not client:
                return False
            if custom_name is not None:
                client.custom_name = custom_name.strip() or client.hostname
            if os_info is not None:
                client.os_info = os_info.strip()
            if ip is not None and ip.strip():
                client.ip = ip.strip()
            self._save_persisted_clients()
            logger.info("Метаданные клиента [%s] обновлены: имя='%s', os='%s', ip='%s'", client_id, client.custom_name, client.os_info, client.ip)
            return True

    async def update_heartbeat(self, client_id: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Обновление таймстемпа активности клиента."""
        async with self._lock:
            if client_id in self._clients:
                client = self._clients[client_id]
                client.last_seen = time.time()
                if data and "audio_enabled" in data:
                    client.audio_enabled = bool(data["audio_enabled"])

    async def set_audio(self, client_id: str, enabled: bool) -> bool:
        """
        Управление звуком: для всех клиентов ('all') или конкретного устройства.
        """
        async with self._lock:
            if client_id == "all":
                self._global_audio_enabled = enabled
                logger.info("Глобальное переключение звука для ВСЕХ клиентов: %s", "ВКЛ" if enabled else "ВЫКЛ")
                for client in self._clients.values():
                    client.audio_enabled = enabled
                    if client.websocket:
                        try:
                            await client.websocket.send_json({
                                "type": "set_audio",
                                "audio_enabled": enabled,
                                "enabled": enabled,
                            })
                        except Exception as e:
                            logger.debug("Не удалось отправить команду клиенту [%s]: %s", client.client_id, e)
                self._save_persisted_clients()
                return True

            # Управление звуком конкретного клиента
            if client_id in self._clients:
                client = self._clients[client_id]
                client.audio_enabled = enabled
                logger.info(
                    "Переключение звука для клиента [%s] (%s): %s",
                    client_id,
                    client.hostname,
                    "ВКЛ" if enabled else "ВЫКЛ",
                )
                if client.websocket:
                    try:
                        await client.websocket.send_json({
                            "type": "set_audio",
                            "audio_enabled": enabled,
                            "enabled": enabled,
                        })
                    except Exception as e:
                        logger.debug("Не удалось отправить команду клиенту [%s]: %s", client_id, e)
                self._save_persisted_clients()
                return True

            logger.warning("Клиент [%s] для управления звуком не найден.", client_id)
            return False

    async def set_stream_allowed(self, client_id: str, allowed: bool) -> bool:
        """Разрешение или запрет трансляции для клиента (или 'all')."""
        async with self._lock:
            if client_id == "all":
                for client in self._clients.values():
                    client.stream_allowed = allowed
                    if client.websocket:
                        try:
                            await client.websocket.send_json({
                                "type": "set_stream_allowed",
                                "allowed": allowed,
                                "stream_allowed": allowed,
                            })
                        except Exception as e:
                            logger.debug("Ошибка отправки set_stream_allowed [%s]: %s", client.client_id, e)
                self._save_persisted_clients()
                logger.info("Вещание для ВСЕХ клиентов: %s", "РАЗРЕШЕНО" if allowed else "ЗАПРЕЩЕНО")
                return True

            client = self._clients.get(client_id)
            if not client:
                return False
            client.stream_allowed = allowed
            if client.websocket:
                try:
                    await client.websocket.send_json({
                        "type": "set_stream_allowed",
                        "allowed": allowed,
                        "stream_allowed": allowed,
                    })
                except Exception as e:
                    logger.debug("Ошибка отправки set_stream_allowed [%s]: %s", client_id, e)
            self._save_persisted_clients()
            logger.info("Вещание для клиента [%s]: %s", client_id, "РАЗРЕШЕНО" if allowed else "ЗАПРЕЩЕНО")
            return True

    async def set_standby(self, client_id: str, standby: bool) -> bool:
        """Перевод клиента в режим ожидания (черный экран, mute) или выход из него."""
        async with self._lock:
            if client_id == "all":
                for client in self._clients.values():
                    client.standby = standby
                    if client.websocket:
                        try:
                            await client.websocket.send_json({
                                "type": "set_standby",
                                "standby": standby,
                            })
                        except Exception as e:
                            logger.debug("Ошибка отправки set_standby [%s]: %s", client.client_id, e)
                self._save_persisted_clients()
                logger.info("Режим Standby для ВСЕХ клиентов: %s", "ВКЛ" if standby else "ВЫКЛ")
                return True

            client = self._clients.get(client_id)
            if not client:
                return False
            client.standby = standby
            if client.websocket:
                try:
                    await client.websocket.send_json({
                        "type": "set_standby",
                        "standby": standby,
                    })
                except Exception as e:
                    logger.debug("Ошибка отправки set_standby [%s]: %s", client_id, e)
            self._save_persisted_clients()
            logger.info("Режим Standby для клиента [%s]: %s", client_id, "ВКЛ" if standby else "ВЫКЛ")
            return True

    async def poweroff_client(self, client_id: str, action: str = "exit_app") -> bool:
        """
        Активное выключение клиента:
        action: 'exit_app' (закрыть клиентское приложение) или 'poweroff' (выключить ПК).
        """
        async with self._lock:
            if client_id == "all":
                success = False
                for client in self._clients.values():
                    if client.websocket:
                        try:
                            await client.websocket.send_json({
                                "type": "shutdown_device",
                                "action": action,
                            })
                            success = True
                        except Exception as e:
                            logger.debug("Ошибка отправки shutdown_device [%s]: %s", client.client_id, e)
                logger.info("Команда выключения (%s) отправлена ВСЕМ подключенным клиентам", action)
                return success

            client = self._clients.get(client_id)
            if not client:
                return False
            if not client.websocket:
                logger.warning("Клиент [%s] не в сети для выполнения выключения", client_id)
                return False
            try:
                await client.websocket.send_json({
                    "type": "shutdown_device",
                    "action": action,
                })
                logger.info("Команда выключения (%s) отправлена клиенту [%s]", action, client_id)
                return True
            except Exception as e:
                logger.error("Ошибка при отправке команды выключения клиенту [%s]: %s", client_id, e)
                return False

    async def get_state(self) -> Dict[str, Any]:
        """Возвращает актуальный список всех клиентов и статус глобального звука."""
        async with self._lock:
            clients_list = [c.to_dict() for c in self._clients.values()]
            connected_count = sum(1 for c in self._clients.values() if c.is_online)
            return {
                "global_audio_enabled": self._global_audio_enabled,
                "total_connected": connected_count,
                "total_clients": len(clients_list),
                "clients": clients_list,
            }


# Глобальный синглтон менеджера клиентов
client_manager = ClientManager()
