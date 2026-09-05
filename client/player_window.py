"""
Виджет полноэкранного плеера для отдельного физического дисплея.
Обеспечивает встраивание libVLC, строгое управление аудио (только primary монитор),
авто-реконнект с темным оверлеем и отказоустойчивость.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette, QScreen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

# Кроссплатформенная инициализация путей libVLC на Windows
if sys.platform.startswith("win"):
    vlc_standard_paths = [
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
    ]
    for p in vlc_standard_paths:
        if os.path.exists(p):
            try:
                os.add_dll_directory(p)
            except AttributeError:
                pass
            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
            break

import vlc  # noqa: E402

logger = logging.getLogger("desktop_player.window")


class VlcEventBridge(QObject):
    """
    Мост для безопасной передачи событий libVLC из нативных C-потоков
    в главный поток Qt через pyqtSignal.
    """
    playback_started = pyqtSignal()
    playback_failed = pyqtSignal(str)
    playback_ended = pyqtSignal()


class PlayerWindow(QMainWindow):
    """
    Полноэкранное окно видеоплеера, привязанное к конкретному QScreen.
    """

    def __init__(
        self,
        screen: QScreen,
        is_primary: bool,
        rtsp_url: str,
        network_caching: int = 300,
        parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)

        self.target_screen = screen
        self.is_primary = is_primary
        self.audio_allowed = True
        self.stream_allowed = True
        self.is_standby = False
        self.rtsp_url = rtsp_url
        self.network_caching = network_caching

        self._is_closing = False
        self._vlc_instance: Optional[vlc.Instance] = None
        self._vlc_player: Optional[vlc.MediaPlayer] = None
        self._event_manager: Optional[vlc.EventManager] = None

        # Мост сигналов
        self._bridge = VlcEventBridge()
        self._bridge.playback_started.connect(self._on_playback_started)
        self._bridge.playback_failed.connect(self._on_playback_failed)
        self._bridge.playback_ended.connect(self._on_playback_ended)

        # Таймер авто-переподключения (3 секунды)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(3000)
        self._reconnect_timer.timeout.connect(self._do_reconnect)

        self._init_window_geometry()
        self._init_ui()
        self._init_vlc()

    def _init_window_geometry(self) -> None:
        """Настройка полноэкранного режима без рамок для конкретного монитора."""
        self.setScreen(self.target_screen)

        # Флаги окна: без системных рамок, поверх остальных окон при старте
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )

        # Абсолютное позиционирование по геометрии экрана
        geom = self.target_screen.geometry()
        self.setGeometry(geom)

        # Черный фон основного окна
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

    def _init_ui(self) -> None:
        """Инициализация контейнера видео и оверлея статуса."""
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        # Макет центрального виджета
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Контейнер для встраивания нативного окна VLC
        self.video_frame = QFrame(self.central_widget)
        # Настройка нативных атрибутов окна для чистого вывода через DirectX/Direct3D без мерцания и черного экрана
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, True)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.main_layout.addWidget(self.video_frame)

        # Информационный оверлей переподключения поверх центрального виджета (не внутри video_frame, избегая конфликта HWND)
        self.overlay_frame = QFrame(self.central_widget)
        self.overlay_frame.setStyleSheet("""
            QFrame {
                background-color: #090d16;
                border: none;
            }
        """)

        overlay_layout = QVBoxLayout(self.overlay_frame)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.setSpacing(14)

        # Стилизованная надпись подключения
        self.status_title = QLabel("Подключение к серверу вещания...")
        font_title = QFont()
        font_title.setPointSize(18)
        font_title.setBold(True)
        self.status_title.setFont(font_title)
        self.status_title.setStyleSheet("color: #38bdf8;")
        self.status_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Дополнительная подсказка
        self.status_hint = QLabel(f"Экран: {self.target_screen.name()} | Звук: {'ВКЛ' if self.is_primary else 'ВЫКЛ (Mute)'}\n«Q» — Настройки | «E» — Выход")
        font_hint = QFont()
        font_hint.setPointSize(11)
        self.status_hint.setFont(font_hint)
        self.status_hint.setStyleSheet("color: #64748b; line-height: 1.4;")
        self.status_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        overlay_layout.addWidget(self.status_title)
        overlay_layout.addWidget(self.status_hint)

    def resizeEvent(self, event) -> None:
        """Синхронизация размеров оверлея с размером окна при изменении геометрии."""
        super().resizeEvent(event)
        self.overlay_frame.setGeometry(0, 0, self.width(), self.height())
        if self.overlay_frame.isVisible():
            self.overlay_frame.raise_()

    def _init_vlc(self) -> None:
        """Инициализация экземпляра libVLC с оптимизированными параметрами."""
        effective_caching = max(800, self.network_caching)
        vlc_args = [
            "--rtsp-tcp",
            f"--network-caching={effective_caching}",
            f"--live-caching={effective_caching}",
            "--clock-jitter=0",
            "--no-video-title-show",
            "--quiet",
            "--no-sub-autodetect-file",
            "--no-snapshot-preview",
            "--no-drop-late-frames",
            "--no-skip-frames",
        ]

        try:
            self._vlc_instance = vlc.Instance(vlc_args)
            self._vlc_player = self._vlc_instance.media_player_new()

            # Привязка нативного дескриптора окна
            win_id = int(self.video_frame.winId())
            if sys.platform.startswith("win"):
                self._vlc_player.set_hwnd(win_id)
            elif sys.platform.startswith("linux"):
                self._vlc_player.set_xwindow(win_id)
            elif sys.platform == "darwin":
                self._vlc_player.set_nsobject(win_id)

            # Настройка подписки на события libVLC
            self._event_manager = self._vlc_player.event_manager()
            self._event_manager.event_attach(vlc.EventType.MediaPlayerPlaying, self._vlc_on_playing)
            self._event_manager.event_attach(vlc.EventType.MediaPlayerEncounteredError, self._vlc_on_error)
            self._event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self._vlc_on_end)

            # Настройка аудио (СТРОГО: только один основной экран со звуком)
            self._apply_audio_routing()

        except Exception as err:
            logger.error("Критическая ошибка инициализации libVLC: %s", err)
            self.status_title.setText(f"Ошибка VLC: {err}")

    def _apply_audio_routing(self) -> None:
        """Управляет звуком: только основной дисплей воспроизводит аудио при наличии разрешения."""
        if not self._vlc_player:
            return

        should_play = self.is_primary and self.audio_allowed
        if should_play:
            self._vlc_player.audio_set_mute(False)
            logger.info("Дисплей [%s] — Основной: звук ВКЛЮЧЕН", self.target_screen.name())
        else:
            self._vlc_player.audio_set_mute(True)
            reason = "Дополнительный экран" if not self.is_primary else "Отключен сервером вещания"
            logger.info("Дисплей [%s] — Звук ЗАГЛУШЕН (%s)", self.target_screen.name(), reason)

        audio_status = "ВКЛ" if should_play else "ВЫКЛ (Mute)"
        self.status_hint.setText(
            f"Экран: {self.target_screen.name()} | Звук: {audio_status}\n«Q» — Настройки | «E» — Выход"
        )

    def set_audio_allowed(self, allowed: bool) -> None:
        """Установка разрешения на воспроизведение звука (по команде с сервера)."""
        self.audio_allowed = allowed
        self._apply_audio_routing()

    # --- Коллбэки VLC (выполняются в нативных C-потоках) ---

    def _vlc_on_playing(self, event) -> None:
        self._bridge.playback_started.emit()

    def _vlc_on_error(self, event) -> None:
        self._bridge.playback_failed.emit("Потеряно соединение с потоком RTSP")

    def _vlc_on_end(self, event) -> None:
        self._bridge.playback_ended.emit()

    # --- Слоты Qt (выполняются в главном GUI потоке) ---

    def _on_playback_started(self) -> None:
        """Поток успешно запущен — скрываем оверлей подключения."""
        logger.info("Поток успешно запущен на экране [%s]", self.target_screen.name())
        self.overlay_frame.hide()
        self._reconnect_timer.stop()

    def _on_playback_failed(self, reason: str) -> None:
        """Сбой воспроизведения — отображаем оверлей и запускаем реконнект."""
        if self._is_closing:
            return
        logger.warning("Сбой воспроизведения на экране [%s]: %s", self.target_screen.name(), reason)
        self.status_title.setText("Подключение к серверу вещания...")
        self.overlay_frame.show()
        self.overlay_frame.raise_()
        if not self._reconnect_timer.isActive():
            self._reconnect_timer.start()

    def _on_playback_ended(self) -> None:
        """Поток завершился (смена трека в плейлисте сервера) — быстрое бесшовное переподключение."""
        if self._is_closing:
            return
        logger.info("Поток завершился на экране [%s]. Бесшовный переход к следующему треку...", self.target_screen.name())
        # При штатной смене треков в очереди сервера следующий трек начинается через доли секунды.
        # Запускаем переподключение через 600 мс для минимизации задержки на экране:
        QTimer.singleShot(600, self.play)

    def _do_reconnect(self) -> None:
        """Попытка переподключения по таймеру."""
        if self._is_closing:
            return
        logger.info("Попытка переподключения к %s на экране [%s]...", self.rtsp_url, self.target_screen.name())
        self.play()

    # --- Публичные методы управления ---

    def play(self, rtsp_url: Optional[str] = None) -> None:
        """Запуск или перезапуск воспроизведения RTSP потока."""
        if self._is_closing or not self._vlc_instance or not self._vlc_player:
            return

        if self.is_standby:
            logger.debug("Экран [%s] находится в режиме Standby, воспроизведение пропущено.", self.target_screen.name())
            self.overlay_frame.hide()
            if self._vlc_player:
                try:
                    self._vlc_player.audio_set_mute(True)
                    self._vlc_player.stop()
                except Exception:
                    pass
            return

        if not self.stream_allowed:
            logger.info("Вещание для экрана [%s] запрещено администратором.", self.target_screen.name())
            self.status_title.setText("Вещание приостановлено администратором")
            self.overlay_frame.show()
            self.overlay_frame.raise_()
            if self._vlc_player:
                try:
                    self._vlc_player.audio_set_mute(True)
                    self._vlc_player.stop()
                except Exception:
                    pass
            return

        if rtsp_url:
            self.rtsp_url = rtsp_url

        if not self.rtsp_url or "://" not in self.rtsp_url:
            logger.warning("Некорректный RTSP URL для запуска: '%s'", self.rtsp_url)
            self.status_title.setText("Ожидание настройки адреса сервера (клавиша 'Q')...")
            self.overlay_frame.show()
            self.overlay_frame.raise_()
            return

        try:
            self.overlay_frame.show()
            self.overlay_frame.raise_()
            self.status_title.setText("Подключение к серверу вещания...")

            # Остановка предыдущего медиа
            self._vlc_player.stop()

            # Создание нового медиаресурса с гарантированным буфером
            effective_caching = max(800, self.network_caching)
            media = self._vlc_instance.media_new(self.rtsp_url)
            media.add_option(f":network-caching={effective_caching}")
            media.add_option(f":live-caching={effective_caching}")
            media.add_option(":rtsp-tcp")
            media.add_option(":clock-jitter=0")
            self._vlc_player.set_media(media)

            # Гарантируем актуальную привязку дескриптора нативного окна
            win_id = int(self.video_frame.winId())
            if sys.platform.startswith("win"):
                self._vlc_player.set_hwnd(win_id)
            elif sys.platform.startswith("linux"):
                self._vlc_player.set_xwindow(win_id)
            elif sys.platform == "darwin":
                self._vlc_player.set_nsobject(win_id)

            # Запуск воспроизведения
            self._vlc_player.play()

            # Повторное применение маршрутизации звука
            self._apply_audio_routing()

        except Exception as err:
            logger.error("Ошибка при вызове play() на экране [%s]: %s", self.target_screen.name(), err)
            self._on_playback_failed(str(err))

    def pause(self) -> None:
        """Временное глушение звука и остановка таймера реконнекта (при открытии окна настроек)."""
        self._reconnect_timer.stop()
        if self._vlc_player:
            try:
                # ВАЖНО: Для живых сетевых RTSP-потоков нельзя вызывать self._vlc_player.pause(),
                # так как это приводит к зависанию потока сокетов libVLC (live555) и взаимной блокировке (Deadlock)
                # при последующем вызове stop()/play(). Достаточно заглушить аудиодорожку.
                self._vlc_player.audio_set_mute(True)
            except Exception:
                pass

    def resume(self) -> None:
        """Возобновление нормального режима воспроизведения после закрытия настроек."""
        if self._vlc_player:
            try:
                if not self._vlc_player.is_playing():
                    self.play()
                else:
                    self._apply_audio_routing()
            except Exception:
                pass

    def stop(self) -> None:
        """Полная остановка воспроизведения."""
        self._reconnect_timer.stop()
        if self._vlc_player:
            try:
                self._vlc_player.stop()
            except Exception:
                pass

    def update_primary_status(self, is_primary: bool) -> None:
        """Обновление статуса основного экрана и перенастройка звука."""
        self.is_primary = is_primary
        self._apply_audio_routing()

    def set_standby(self, enabled: bool) -> None:
        """Управление режимом ожидания (формальное выключение, сплошной черный экран, mute)."""
        self.is_standby = enabled
        if enabled:
            self._reconnect_timer.stop()
            self.overlay_frame.hide()
            if self._vlc_player:
                try:
                    self._vlc_player.audio_set_mute(True)
                    self._vlc_player.stop()
                except Exception:
                    pass
        else:
            if self.stream_allowed:
                self.play()

    def set_stream_allowed(self, allowed: bool) -> None:
        """Управление разрешением на вещание с сервера."""
        self.stream_allowed = allowed
        if not allowed:
            self._reconnect_timer.stop()
            self.status_title.setText("Вещание приостановлено администратором")
            self.overlay_frame.show()
            self.overlay_frame.raise_()
            if self._vlc_player:
                try:
                    self._vlc_player.audio_set_mute(True)
                    self._vlc_player.stop()
                except Exception:
                    pass
        else:
            if not self.is_standby:
                self.play()

    def cleanup(self) -> None:
        """Корректное освобождение ресурсов окна и плеера."""
        self._is_closing = True
        self._reconnect_timer.stop()

        if self._event_manager and self._vlc_player:
            try:
                self._event_manager.event_detach(vlc.EventType.MediaPlayerPlaying)
                self._event_manager.event_detach(vlc.EventType.MediaPlayerEncounteredError)
                self._event_manager.event_detach(vlc.EventType.MediaPlayerEndReached)
            except Exception:
                pass

        if self._vlc_player:
            try:
                self._vlc_player.stop()
                self._vlc_player.release()
            except Exception:
                pass
            self._vlc_player = None

        if self._vlc_instance:
            try:
                self._vlc_instance.release()
            except Exception:
                pass
            self._vlc_instance = None

    def closeEvent(self, event) -> None:
        """Обработка события закрытия окна."""
        self.cleanup()
        super().closeEvent(event)
