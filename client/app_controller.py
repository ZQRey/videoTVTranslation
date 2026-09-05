"""
Контроллер клиентского приложения.
Отвечает за обнаружение всех физических мониторов, хотплаг экранов (screenAdded/Removed),
строгую маршрутизацию звука (только primary монитор), глобальный перехват клавиши 'Q'
и синхронизацию окон воспроизведения.
"""

from __future__ import annotations

import json
import logging
import platform
import signal
import socket
import subprocess
import sys
import uuid
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QScreen
from PyQt6.QtWebSockets import QWebSocket
from PyQt6.QtWidgets import QApplication

from config import ClientConfig, ConfigManager
from player_window import PlayerWindow
from settings_dialog import SettingsDialog

logger = logging.getLogger("desktop_player.controller")


def get_detailed_os_info() -> str:
    """
    Возвращает подробную и точную информацию об операционной системе:
    - Windows 11 (Build >= 22000) или Windows 10 / 8.1 / 7 с номером сборки
    - Linux дистрибутив (например, Ubuntu 22.04 LTS) и версия ядра
    - macOS с номером версии
    """
    system = platform.system()
    if system == "Windows":
        try:
            win_ver = sys.getwindowsversion()
            build = getattr(win_ver, "build", 0)
            if win_ver.major == 10:
                win_name = "Windows 11" if build >= 22000 else "Windows 10"
            elif win_ver.major == 6:
                names = {3: "Windows 8.1", 2: "Windows 8", 1: "Windows 7", 0: "Windows Vista"}
                win_name = names.get(win_ver.minor, "Windows")
            else:
                win_name = f"Windows {platform.release()}"
            return f"{win_name} (Build {build})"
        except Exception:
            return f"Windows {platform.release()}"
    elif system == "Linux":
        try:
            if hasattr(platform, "freedesktop_os_release"):
                info = platform.freedesktop_os_release()
                pretty = info.get("PRETTY_NAME") or info.get("NAME")
                if pretty:
                    return f"{pretty} ({platform.release()})"
            os_release = Path("/etc/os-release")
            if os_release.exists():
                for line in os_release.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("PRETTY_NAME="):
                        val = line.split("=", 1)[1].strip('"\'')
                        return f"{val} ({platform.release()})"
        except Exception:
            pass
        return f"Linux {platform.release()}"
    elif system == "Darwin":
        mac_ver = platform.mac_ver()[0]
        return f"macOS {mac_ver}" if mac_ver else "macOS"
    return f"{system} {platform.release()}".strip() or "Unknown OS"


class GlobalKeyFilter(QObject):
    """
    Глобальный фильтр событий клавиатуры на уровне всего приложения.
    Перехватывает нажатия:
    - 'Q' / 'й' — открытие диалога настроек подключения
    - 'E' / 'у' — быстрое завершение работы приложения
    """

    def __init__(
        self,
        settings_callback,
        exit_callback: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings_callback = settings_callback
        self._exit_callback = exit_callback

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key_event: QKeyEvent = event
            key = key_event.key()
            text = key_event.text().lower()

            # 1. Проверка клавиши Q (английская 'q' или русская 'й') — вызов окна настроек
            if key == Qt.Key.Key_Q or text in ("q", "й"):
                logger.info("Обнаружено нажатие горячей клавиши 'Q' (символ '%s'). Вызов окна настроек.", text)
                if self._settings_callback:
                    QTimer.singleShot(0, self._settings_callback)
                return True

            # 2. Проверка клавиши E (английская 'e' или русская 'у') — завершение работы клиента
            if key == Qt.Key.Key_E or text in ("e", "у"):
                logger.info("Обнаружено нажатие горячей клавиши 'E' (символ '%s'). Завершение работы клиента.", text)
                if self._exit_callback:
                    QTimer.singleShot(0, self._exit_callback)
                return True

        return super().eventFilter(watched, event)


class AppController(QObject):
    """
    Основной контроллер мультиэкранного плеера.
    """

    def __init__(self, config_manager: ConfigManager, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.windows: Dict[QScreen, PlayerWindow] = {}
        self._is_settings_open = False
        self._settings_dialog: Optional[SettingsDialog] = None
        self.server_audio_enabled: bool = True
        self.stream_allowed: bool = True
        self.is_standby: bool = False
        self._standby_by_local_schedule: bool = False
        self.client_id = self.config.client_id

        # Установка глобального перехватчика горячих клавиш 'Q' (настройки) и 'E' (выход)
        self._key_filter = GlobalKeyFilter(self.open_settings, self.shutdown, self)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self._key_filter)

        # Сетевое соединение WebSocket с сервером вещания
        self._ws: Optional[QWebSocket] = None
        self._ws_reconnect_timer = QTimer(self)
        self._ws_reconnect_timer.setInterval(3000)
        self._ws_reconnect_timer.setSingleShot(True)
        self._ws_reconnect_timer.timeout.connect(self._connect_websocket)

        self._ws_heartbeat_timer = QTimer(self)
        self._ws_heartbeat_timer.setInterval(5000)
        self._ws_heartbeat_timer.timeout.connect(self._send_heartbeat)

        # Таймер локального контроля персонального расписания
        self._local_schedule_timer = QTimer(self)
        self._local_schedule_timer.setInterval(3000)
        self._local_schedule_timer.timeout.connect(self._check_local_schedule)
        self._local_schedule_timer.start()

        # Таймер Heartbeat для гарантированной обработки SIGINT (Ctrl+C) в Python/Qt
        self._sigint_timer = QTimer(self)
        self._sigint_timer.setInterval(400)
        self._sigint_timer.timeout.connect(lambda: None)
        self._sigint_timer.start()

        # Регистрация обработчика Ctrl+C
        signal.signal(signal.SIGINT, self._handle_sigint)

        # Подписка на сигналы изменения экранов (горячее подключение / отключение)
        self._subscribe_screen_events()

    @property
    def config(self) -> ClientConfig:
        return self.config_manager.config

    def _subscribe_screen_events(self) -> None:
        """Подписка на динамические события дисплеев ОС."""
        qapp = QGuiApplication.instance()
        if qapp:
            qapp.screenAdded.connect(self._on_screen_added)
            qapp.screenRemoved.connect(self._on_screen_removed)
            qapp.primaryScreenChanged.connect(self._on_primary_screen_changed)

    def start(self) -> None:
        """
        Запуск плеера на всех подключенных мониторах.
        При первом запуске без настроенного сервера сразу открывает окно настроек.
        """
        screens = QGuiApplication.screens()
        primary_screen = QGuiApplication.primaryScreen()

        logger.info("Обнаружено физических экранов: %d. Основной экран: %s", len(screens), primary_screen.name() if primary_screen else "None")

        # Создаем и открываем окна плеера на всех экранах
        for screen in screens:
            self._create_player_window(screen, primary_screen)

        # Если адрес сервера не настроен — открываем диалог настроек
        if not self.config.is_configured():
            logger.info("Адрес сервера не задан. Запуск мастера первоначальной настройки.")
            QTimer.singleShot(150, self.open_settings)
        else:
            # Запуск воспроизведения на всех экранах
            self._start_playback_all()
            # Подключение WebSocket к серверу для телеметрии и управления
            self._connect_websocket()

    def _create_player_window(self, screen: QScreen, primary_screen: Optional[QScreen]) -> PlayerWindow:
        """Создает и позиционирует окно плеера на заданном экране."""
        is_primary = (screen == primary_screen)
        logger.info("Создание окна плеера для экрана [%s] (Primary: %s, Geometry: %s)", screen.name(), is_primary, screen.geometry())

        window = PlayerWindow(
            screen=screen,
            is_primary=is_primary,
            rtsp_url=self.config.rtsp_url,
            network_caching=self.config.network_caching
        )
        window.set_stream_allowed(self.stream_allowed)
        window.set_standby(self.is_standby)
        self.windows[screen] = window

        # Отображение в полноэкранном режиме
        window.showFullScreen()
        return window

    def _start_playback_all(self) -> None:
        """Запускает воспроизведение на всех активных окнах плееров."""
        rtsp_url = self.config.rtsp_url
        logger.info("Запуск вещания потока: %s на %d экранах", rtsp_url, len(self.windows))
        for screen, window in self.windows.items():
            window.play(rtsp_url)

    # --- Обработка хотплага дисплеев ---

    def _on_screen_added(self, screen: QScreen) -> None:
        """Реакция на подключение нового монитора/телевизора к ПК."""
        if screen in self.windows:
            return

        primary_screen = QGuiApplication.primaryScreen()
        logger.info("ХОТПЛАГ: Обнаружен новый экран [%s]. Создание окна плеера.", screen.name())

        window = self._create_player_window(screen, primary_screen)
        if self.config.is_configured():
            window.play(self.config.rtsp_url)

    def _on_screen_removed(self, screen: QScreen) -> None:
        """Реакция на физическое отключение дисплея."""
        logger.info("ХОТПЛАГ: Экран [%s] отключен. Закрытие соответствующего окна.", screen.name())
        window = self.windows.pop(screen, None)
        if window:
            window.cleanup()
            window.close()

        # Если отключился основной экран — переопределяем звук
        self._refresh_audio_routing()

    def _on_primary_screen_changed(self, primary_screen: QScreen) -> None:
        """Реакция на изменение основного монитора в настройках ОС."""
        logger.info("ОС изменила основной экран на [%s]. Обновление маршрутизации аудио.", primary_screen.name())
        self._refresh_audio_routing()

    def _refresh_audio_routing(self) -> None:
        """Гарантирует, что звук включен на primary экране с учетом разрешения сервера."""
        primary_screen = QGuiApplication.primaryScreen()
        for screen, window in self.windows.items():
            is_primary = (screen == primary_screen)
            window.update_primary_status(is_primary)
            window.set_audio_allowed(self.server_audio_enabled)

    # --- Связь с сервером через WebSocket (мониторинг и удаленное управление звуком) ---

    def _connect_websocket(self) -> None:
        """Инициализация и подключение WebSocket к серверу управления."""
        if not self.config.is_configured():
            return

        if self._ws is None:
            self._ws = QWebSocket()
            self._ws.connected.connect(self._on_ws_connected)
            self._ws.disconnected.connect(self._on_ws_disconnected)
            self._ws.textMessageReceived.connect(self._on_ws_message)
            self._ws.errorOccurred.connect(self._on_ws_error)

        ws_url = self.config.ws_client_url
        logger.info("Подключение к серверу управления: %s", ws_url)
        self._ws.open(QUrl(ws_url))

    def _on_ws_connected(self) -> None:
        """Событие успешного подключения WebSocket к серверу."""
        logger.info("WebSocket успешно подключен к серверу управления.")
        self._ws_reconnect_timer.stop()

        # Отправка пакета регистрации устройства
        screens_list = [s.name() for s in QGuiApplication.screens()]
        primary = QGuiApplication.primaryScreen()
        primary_name = primary.name() if primary else ""

        reg_data = {
            "type": "register",
            "client_id": self.client_id,
            "token": getattr(self.config, "token", self.client_id),
            "hostname": socket.gethostname(),
            "os_info": get_detailed_os_info(),
            "screens": screens_list,
            "primary_screen": primary_name,
            "audio_enabled": self.server_audio_enabled,
            "stream_allowed": self.stream_allowed,
            "standby": self.is_standby,
            "schedule_mode": getattr(self.config, "schedule_mode", "global"),
            "schedule_start": getattr(self.config, "schedule_start", "08:00"),
            "schedule_end": getattr(self.config, "schedule_end", "20:00"),
            "schedule_days": getattr(self.config, "schedule_days", [1, 2, 3, 4, 5, 6, 7]),
        }
        self._ws.sendTextMessage(json.dumps(reg_data, ensure_ascii=False))
        self._ws_heartbeat_timer.start()

    def _on_ws_disconnected(self) -> None:
        """Событие разрыва соединения WebSocket."""
        logger.debug("WebSocket отключен от сервера управления. Запуск таймера переподключения.")
        self._ws_heartbeat_timer.stop()
        if self.config.is_configured() and not self._ws_reconnect_timer.isActive():
            self._ws_reconnect_timer.start()

    def _on_ws_error(self, error) -> None:
        """Обработка сетевых ошибок WebSocket."""
        logger.debug("Ошибка WebSocket: %s", error)

    def _send_heartbeat(self) -> None:
        """Периодическая отправка heartbeat пакета активности на сервер."""
        if self._ws and self._ws.isValid():
            hb_data = {
                "type": "heartbeat",
                "client_id": self.client_id,
                "token": getattr(self.config, "token", self.client_id),
                "audio_enabled": self.server_audio_enabled,
                "stream_allowed": self.stream_allowed,
                "standby": self.is_standby,
                "schedule_mode": getattr(self.config, "schedule_mode", "global"),
                "schedule_start": getattr(self.config, "schedule_start", "08:00"),
                "schedule_end": getattr(self.config, "schedule_end", "20:00"),
                "schedule_days": getattr(self.config, "schedule_days", [1, 2, 3, 4, 5, 6, 7]),
            }
            self._ws.sendTextMessage(json.dumps(hb_data))

    def _on_ws_message(self, message: str) -> None:
        """Обработка команд управления от сервера вещания."""
        try:
            msg = json.loads(message)
            msg_type = msg.get("type")

            if msg_type in ("set_audio", "registered", "init_state"):
                if "audio_enabled" in msg:
                    new_state = bool(msg["audio_enabled"])
                elif "enabled" in msg:
                    new_state = bool(msg["enabled"])
                else:
                    new_state = self.server_audio_enabled

                if self.server_audio_enabled != new_state:
                    logger.info(
                        "Получена команда от сервера: переключение звука -> %s",
                        "ВКЛ" if new_state else "ВЫКЛ"
                    )
                    self.server_audio_enabled = new_state
                    self._refresh_audio_routing()

                if "stream_allowed" in msg:
                    new_stream = bool(msg["stream_allowed"])
                    if self.stream_allowed != new_stream:
                        logger.info("Сервер установил разрешение вещания -> %s", new_stream)
                        self.stream_allowed = new_stream
                        for w in self.windows.values():
                            w.set_stream_allowed(new_stream)

                if "standby" in msg:
                    new_standby = bool(msg["standby"])
                    if self.is_standby != new_standby:
                        logger.info("Сервер установил режим Standby -> %s", new_standby)
                        self.is_standby = new_standby
                        for w in self.windows.values():
                            w.set_standby(new_standby)

                # Отправляем подтверждение статуса при индивидуальной команде set_audio
                if msg_type == "set_audio" and self._ws and self._ws.isValid():
                    resp = {
                        "type": "status_update",
                        "client_id": self.client_id,
                        "audio_enabled": self.server_audio_enabled,
                    }
                    self._ws.sendTextMessage(json.dumps(resp))

            elif msg_type == "set_stream_allowed":
                new_stream = bool(msg.get("allowed", msg.get("stream_allowed", True)))
                logger.info("Получена команда сервера: вещание -> %s", "РАЗРЕШЕНО" if new_stream else "ЗАПРЕЩЕНО")
                self.stream_allowed = new_stream
                for w in self.windows.values():
                    w.set_stream_allowed(new_stream)
                if self._ws and self._ws.isValid():
                    resp = {
                        "type": "status_update",
                        "client_id": self.client_id,
                        "stream_allowed": self.stream_allowed,
                    }
                    self._ws.sendTextMessage(json.dumps(resp))

            elif msg_type == "set_standby":
                new_standby = bool(msg.get("standby", False))
                logger.info("Получена команда сервера: режим Standby -> %s", "ВКЛ" if new_standby else "ВЫКЛ")
                self.is_standby = new_standby
                for w in self.windows.values():
                    w.set_standby(new_standby)
                if self._ws and self._ws.isValid():
                    resp = {
                        "type": "status_update",
                        "client_id": self.client_id,
                        "standby": self.is_standby,
                    }
                    self._ws.sendTextMessage(json.dumps(resp))

            elif msg_type == "shutdown_device":
                action = msg.get("action", "exit_app")
                logger.warning("Получена команда удаленного выключения устройства (действие: %s)", action)
                if action == "exit_app":
                    QTimer.singleShot(0, self.shutdown)
                elif action == "poweroff":
                    QTimer.singleShot(0, self._poweroff_system)

        except Exception as err:
            logger.error("Ошибка парсинга команды WebSocket от сервера: %s", err)

    # --- Управление окном настроек (Клавиша 'Q') ---

    def open_settings(self) -> None:
        """
        Открывает диалог настроек сервера поверх основного экрана.
        При открытии глушит/приостанавливает воспроизведение, при закрытии обновляет стрим.
        """
        if self._is_settings_open:
            return

        self._is_settings_open = True
        logger.info("Открытие диалогового окна настроек...")

        # Приостанавливаем / глушим воспроизведение
        for window in self.windows.values():
            window.pause()

        # Поиск родительского окна на основном экране для модальности
        primary_screen = QGuiApplication.primaryScreen()
        parent_window = self.windows.get(primary_screen, None)

        self._settings_dialog = SettingsDialog(
            current_host=self.config.server_host,
            current_port=self.config.rtsp_port,
            current_path=self.config.stream_path,
            current_schedule_mode=getattr(self.config, "schedule_mode", "global"),
            current_schedule_start=getattr(self.config, "schedule_start", "08:00"),
            current_schedule_end=getattr(self.config, "schedule_end", "20:00"),
            current_schedule_days=getattr(self.config, "schedule_days", [1, 2, 3, 4, 5, 6, 7]),
            parent=parent_window
        )

        # Центрирование диалога на основном экране
        if primary_screen:
            center = primary_screen.geometry().center()
            self._settings_dialog.move(
                center.x() - self._settings_dialog.width() // 2,
                center.y() - self._settings_dialog.height() // 2
            )

        old_url = self.config.rtsp_url

        # Модальный запуск
        result = self._settings_dialog.exec()

        if result == SettingsDialog.DialogCode.Accepted:
            settings = self._settings_dialog.get_settings()
            host, port, path = settings.host, settings.port, settings.path
            logger.info("Настройки сохранены пользователем: %s:%d/%s (режим расписания: %s)", host, port, path, settings.schedule_mode)

            # Обновление и запись конфигурации
            self.config_manager.update(
                server_host=host,
                rtsp_port=port,
                stream_path=path,
                network_caching=self.config.network_caching,
                schedule_mode=settings.schedule_mode,
                schedule_start=settings.schedule_start,
                schedule_end=settings.schedule_end,
                schedule_days=settings.schedule_days,
            )
            new_url = self.config.rtsp_url

            # Отправка обновленных данных на сервер по WS
            if self._ws and self._ws.isValid():
                self._send_heartbeat()

            # Немедленная проверка локального расписания
            self._check_local_schedule()

            if new_url == old_url:
                logger.info("URL потока не изменился (%s). Возобновление текущего воспроизведения без перезапуска VLC.", new_url)
                for window in self.windows.values():
                    window.resume()
            else:
                logger.info("URL потока изменился: %s -> %s. Выполняется переподключение всех экранов.", old_url, new_url)
                # Откладываем запуск воспроизведения через singleShot, чтобы модальное окно успело полностью закрыться
                QTimer.singleShot(50, self._start_playback_all)
        else:
            logger.info("Окно настроек закрыто без сохранения изменений.")
            # Возобновление воспроизведения
            for window in self.windows.values():
                window.resume()

        self._settings_dialog = None
        self._is_settings_open = False

    @staticmethod
    def _is_time_in_range(cur_t: dt_time, start_str: str, end_str: str) -> bool:
        """Проверка попадания времени cur_t в интервал start_str - end_str."""
        try:
            sh, sm = map(int, start_str.split(":", 1))
            eh, em = map(int, end_str.split(":", 1))
            start_t = dt_time(sh, sm)
            end_t = dt_time(eh, em)
            if start_t <= end_t:
                return start_t <= cur_t <= end_t
            else:
                return cur_t >= start_t or cur_t <= end_t
        except Exception:
            return True

    def _check_local_schedule(self) -> None:
        """Периодическая проверка автономного локального расписания клиента."""
        mode = getattr(self.config, "schedule_mode", "global")
        if mode != "interval":
            if self._standby_by_local_schedule and self.is_standby:
                self._standby_by_local_schedule = False
                self.set_standby(False)
            return

        now = datetime.now()
        weekday = now.isoweekday()
        days = getattr(self.config, "schedule_days", [1, 2, 3, 4, 5, 6, 7]) or [1, 2, 3, 4, 5, 6, 7]
        if weekday not in days:
            in_window = False
        else:
            start_str = getattr(self.config, "schedule_start", "08:00")
            end_str = getattr(self.config, "schedule_end", "20:00")
            in_window = self._is_time_in_range(now.time(), start_str, end_str)

        if not in_window:
            if not self.is_standby:
                self._standby_by_local_schedule = True
                logger.info("🌙 Локальное расписание клиента: экран переведен в режим Standby (чёрный экран).")
                self.set_standby(True)
        else:
            if self.is_standby and self._standby_by_local_schedule:
                self._standby_by_local_schedule = False
                logger.info("☀️ Локальное расписание клиента: эфир возобновлен.")
                self.set_standby(False)

    # --- Завершение работы ---

    def _handle_sigint(self, signum, frame) -> None:
        """Обработка сигнала прерывания SIGINT (Ctrl+C)."""
        logger.info("Получен сигнал завершения SIGINT (Ctrl+C). Выполняется штатная остановка...")
        self.shutdown()

    def shutdown(self) -> None:
        """Штатная остановка всех окон и освобождение ресурсов."""
        self._sigint_timer.stop()
        self._ws_reconnect_timer.stop()
        self._ws_heartbeat_timer.stop()

        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        for screen, window in list(self.windows.items()):
            try:
                window.cleanup()
                window.close()
            except Exception as err:
                logger.error("Ошибка при закрытии окна [%s]: %s", screen.name(), err)

        self.windows.clear()

        app = QApplication.instance()
        if app:
            app.quit()

    def _poweroff_system(self) -> None:
        """Завершение работы операционной системы."""
        logger.info("Выключение операционной системы...")
        self.shutdown()
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["shutdown", "/s", "/t", "2"], check=False)
            elif sys.platform.startswith("linux"):
                subprocess.run(["systemctl", "poweroff"], check=False)
        except Exception as err:
            logger.error("Ошибка при вызове команды выключения ОС: %s", err)
