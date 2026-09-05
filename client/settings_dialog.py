"""
Модальное окно настроек подключения к серверу вещания.
Вызывается по горячей клавише 'Q' во время воспроизведения или автоматически при первом запуске.
"""

from __future__ import annotations

from typing import Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class SettingsDialog(QDialog):
    """
    Модальный диалог настроек сервера вещания в современном темном стиле.
    """

    def __init__(
        self,
        current_host: str = "",
        current_port: int = 8554,
        current_path: str = "live",
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("Настройки подключения к серверу")
        self.setModal(True)
        self.setFixedWidth(460)

        # Диалог должен отображаться поверх полноэкранных плееров
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._init_ui(current_host, current_port, current_path)
        self._apply_dark_theme()

    def _init_ui(self, host: str, port: int, path: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Заголовок и пояснение
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        title_label = QLabel("Параметры вещания")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setObjectName("dialogTitle")

        subtitle_label = QLabel("Укажите адрес сервера MediaMTX для синхронного воспроизведения.")
        subtitle_label.setObjectName("dialogSubtitle")
        subtitle_label.setWordWrap(True)

        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        layout.addLayout(header_layout)

        # Поле ввода хоста
        host_layout = QVBoxLayout()
        host_layout.setSpacing(6)
        host_label = QLabel("IP-адрес или домен сервера:")
        self.host_input = QLineEdit(host)
        self.host_input.setPlaceholderText("Например: 127.0.0.1 или 192.168.1.100")
        self.host_input.setObjectName("inputField")
        host_layout.addWidget(host_label)
        host_layout.addWidget(self.host_input)
        layout.addLayout(host_layout)

        # Поле ввода порта и пути в одну строку
        net_layout = QHBoxLayout()
        net_layout.setSpacing(14)

        port_layout = QVBoxLayout()
        port_layout.setSpacing(6)
        port_label = QLabel("Порт RTSP:")
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(port if port > 0 else 8554)
        self.port_input.setObjectName("spinField")
        port_layout.addWidget(port_label)
        port_layout.addWidget(self.port_input)
        net_layout.addLayout(port_layout, stretch=1)

        path_layout = QVBoxLayout()
        path_layout.setSpacing(6)
        path_label = QLabel("Имя потока (Path):")
        self.path_input = QLineEdit(path if path else "live")
        self.path_input.setPlaceholderText("live")
        self.path_input.setObjectName("inputField")
        path_layout.addWidget(path_label)
        path_layout.addWidget(self.path_input)
        net_layout.addLayout(path_layout, stretch=2)

        layout.addLayout(net_layout)

        # Кнопка быстрой настройки на локальный сервер
        self.btn_localhost = QPushButton("⚡ Использовать локальный сервер (127.0.0.1)")
        self.btn_localhost.setObjectName("btnLocalhost")
        self.btn_localhost.clicked.connect(self._set_localhost)
        layout.addWidget(self.btn_localhost)

        # Сообщение об ошибке валидации
        self.error_label = QLabel("")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        layout.addSpacing(8)

        # Кнопки действий
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setObjectName("btnCancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_connect = QPushButton("Подключиться")
        self.btn_connect.setObjectName("btnConnect")
        self.btn_connect.setDefault(True)
        self.btn_connect.clicked.connect(self._validate_and_accept)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_connect)
        layout.addLayout(btn_layout)

        # Фокус на поле ввода хоста
        self.host_input.setFocus()

    def _set_localhost(self) -> None:
        """Быстрая установка локального сервера."""
        self.host_input.setText("127.0.0.1")
        self.port_input.setValue(8554)
        self.path_input.setText("live")
        self.error_label.setVisible(False)

    def _validate_and_accept(self) -> None:
        """Валидирует введенные данные перед закрытием диалога."""
        host = self.host_input.text().strip()
        if not host:
            self.error_label.setText("Пожалуйста, укажите адрес сервера вещания.")
            self.error_label.setVisible(True)
            self.host_input.setFocus()
            return

        self.error_label.setVisible(False)
        self.accept()

    def get_settings(self) -> Tuple[str, int, str]:
        """Возвращает кортеж (host, port, path)."""
        host = self.host_input.text().strip()
        port = self.port_input.value()
        path = self.path_input.text().strip().lstrip("/") or "live"
        return host, port, path

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Обработка клавиш Enter и Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._validate_and_accept()
        else:
            super().keyPressEvent(event)

    def _apply_dark_theme(self) -> None:
        """Применяет элегантные стили темной темы."""
        self.setStyleSheet("""
            QDialog {
                background-color: #111827;
                color: #f3f4f6;
                border: 1px solid #374151;
                border-radius: 10px;
            }
            QLabel {
                color: #9ca3af;
                font-size: 13px;
            }
            #dialogTitle {
                color: #f9fafb;
                font-weight: 700;
            }
            #dialogSubtitle {
                color: #6b7280;
                font-size: 12px;
            }
            QLineEdit, QSpinBox {
                background-color: #1f2937;
                color: #f9fafb;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                selection-background-color: #2563eb;
            }
            QLineEdit:focus, QSpinBox:focus {
                border: 1px solid #3b82f6;
                background-color: #1e293b;
            }
            #btnLocalhost {
                background-color: #1e293b;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 12px;
                font-weight: 600;
                text-align: center;
            }
            #btnLocalhost:hover {
                background-color: #0369a1;
                color: #ffffff;
            }
            #btnConnect {
                background-color: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 10px 18px;
                font-size: 13px;
                font-weight: 600;
            }
            #btnConnect:hover {
                background-color: #1d4ed8;
            }
            #btnConnect:pressed {
                background-color: #1e40af;
            }
            #btnCancel {
                background-color: #374151;
                color: #e5e7eb;
                border: none;
                border-radius: 6px;
                padding: 10px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            #btnCancel:hover {
                background-color: #4b5563;
            }
            #errorLabel {
                color: #ef4444;
                font-size: 12px;
                font-weight: 500;
            }
        """)
