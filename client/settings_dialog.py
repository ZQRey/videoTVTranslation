"""
Модальное окно настроек подключения к серверу вещания и расписания экрана.
Вызывается по горячей клавише 'Q' во время воспроизведения или автоматически при первом запуске.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QTime, Qt
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


class SettingsResult(tuple):
    """
    Кортеж настроек, совместимый со старым форматом (host, port, path)
    и содержащий расширенные свойства расписания.
    """
    def __new__(
        cls,
        host: str,
        port: int,
        path: str,
        schedule_mode: str = "global",
        schedule_start: str = "08:00",
        schedule_end: str = "20:00",
        schedule_days: Optional[List[int]] = None,
    ):
        return super().__new__(cls, (host, port, path))

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        schedule_mode: str = "global",
        schedule_start: str = "08:00",
        schedule_end: str = "20:00",
        schedule_days: Optional[List[int]] = None,
    ):
        self.host = host
        self.port = port
        self.path = path
        self.schedule_mode = schedule_mode
        self.schedule_start = schedule_start
        self.schedule_end = schedule_end
        self.schedule_days = schedule_days or [1, 2, 3, 4, 5, 6, 7]

    @property
    def full(self) -> Tuple[str, int, str, str, str, str, List[int]]:
        return (
            self.host,
            self.port,
            self.path,
            self.schedule_mode,
            self.schedule_start,
            self.schedule_end,
            self.schedule_days,
        )


class SettingsDialog(QDialog):
    """
    Модальный диалог настроек сервера вещания и расписания в современном темном стиле.
    """

    def __init__(
        self,
        current_host: str = "",
        current_port: int = 8554,
        current_path: str = "live",
        current_schedule_mode: str = "global",
        current_schedule_start: str = "08:00",
        current_schedule_end: str = "20:00",
        current_schedule_days: Optional[List[int]] = None,
        parent: QWidget | None = None,
        **kwargs,
    ) -> None:
        super().__init__(parent)
        if "schedule_mode" in kwargs:
            current_schedule_mode = kwargs["schedule_mode"]
        if "schedule_start" in kwargs:
            current_schedule_start = kwargs["schedule_start"]
        if "schedule_end" in kwargs:
            current_schedule_end = kwargs["schedule_end"]
        if "schedule_days" in kwargs:
            current_schedule_days = kwargs["schedule_days"]

        self.setWindowTitle("Настройки подключения и расписания")
        self.setModal(True)
        self.setFixedWidth(500)

        # Диалог должен отображаться поверх полноэкранных плееров
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._init_ui(
            current_host,
            current_port,
            current_path,
            current_schedule_mode,
            current_schedule_start,
            current_schedule_end,
            current_schedule_days,
        )
        self._apply_dark_theme()

    def _init_ui(
        self,
        host: str,
        port: int,
        path: str,
        schedule_mode: str,
        schedule_start: str,
        schedule_end: str,
        schedule_days: Optional[List[int]],
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # Заголовок и пояснение
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        title_label = QLabel("Параметры вещания и экрана")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setObjectName("dialogTitle")

        subtitle_label = QLabel("Настройка подключения к серверу MediaMTX и графика работы экрана.")
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

        # =========================================================================
        # Блок настройки расписания вещания для данного клиента
        # =========================================================================
        sched_frame = QFrame()
        sched_frame.setObjectName("scheduleFrame")
        sched_layout = QVBoxLayout(sched_frame)
        sched_layout.setContentsMargins(14, 12, 14, 12)
        sched_layout.setSpacing(10)

        sched_header = QLabel("Расписание вещания для этого экрана")
        sched_header.setObjectName("sectionHeader")
        sched_layout.addWidget(sched_header)

        # Выбор режима расписания (Радиокнопки)
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(12)
        self.mode_group = QButtonGroup(self)

        self.rb_global = QRadioButton("Серверное")
        self.rb_global.setObjectName("radioMode")
        self.rb_24_7 = QRadioButton("24/7 Круглосуточно")
        self.rb_24_7.setObjectName("radioMode")
        self.rb_interval = QRadioButton("Свой интервал")
        self.rb_interval.setObjectName("radioMode")

        # Алиасы для обратной совместимости
        self.radio_global = self.rb_global
        self.radio_24_7 = self.rb_24_7
        self.radio_interval = self.rb_interval

        self.mode_group.addButton(self.rb_global, 1)
        self.mode_group.addButton(self.rb_24_7, 2)
        self.mode_group.addButton(self.rb_interval, 3)

        mode_layout.addWidget(self.rb_global)
        mode_layout.addWidget(self.rb_24_7)
        mode_layout.addWidget(self.rb_interval)
        sched_layout.addLayout(mode_layout)

        # Контейнер для настройки интервала времени и дней недели
        self.interval_container = QWidget()
        interval_layout = QVBoxLayout(self.interval_container)
        interval_layout.setContentsMargins(0, 4, 0, 0)
        interval_layout.setSpacing(8)

        times_layout = QHBoxLayout()
        times_layout.setSpacing(14)

        start_col = QVBoxLayout()
        start_col.setSpacing(4)
        start_lbl = QLabel("Включение эфира:")
        start_lbl.setObjectName("subLabel")
        self.time_start = QTimeEdit()
        self.time_start.setDisplayFormat("HH:mm")
        t_start = QTime.fromString(schedule_start, "HH:mm")
        self.time_start.setTime(t_start if t_start.isValid() else QTime(8, 0))
        start_col.addWidget(start_lbl)
        start_col.addWidget(self.time_start)
        times_layout.addLayout(start_col)

        end_col = QVBoxLayout()
        end_col.setSpacing(4)
        end_lbl = QLabel("Переход в Standby:")
        end_lbl.setObjectName("subLabel")
        self.time_end = QTimeEdit()
        self.time_end.setDisplayFormat("HH:mm")
        t_end = QTime.fromString(schedule_end, "HH:mm")
        self.time_end.setTime(t_end if t_end.isValid() else QTime(20, 0))
        end_col.addWidget(end_lbl)
        end_col.addWidget(self.time_end)
        times_layout.addLayout(end_col)

        interval_layout.addLayout(times_layout)

        # Кнопки дней недели (Пн..Вс)
        days_lbl = QLabel("Активные дни недели:")
        days_lbl.setObjectName("subLabel")
        interval_layout.addWidget(days_lbl)

        days_layout = QHBoxLayout()
        days_layout.setSpacing(5)
        self.day_buttons: List[QPushButton] = []
        active_days = schedule_days or [1, 2, 3, 4, 5, 6, 7]
        day_names = [("Пн", 1), ("Вт", 2), ("Ср", 3), ("Чт", 4), ("Пт", 5), ("Сб", 6), ("Вс", 7)]
        for name, num in day_names:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(num in active_days)
            btn.setProperty("day_num", num)
            btn.setObjectName("dayBtn")
            self.day_buttons.append(btn)
            days_layout.addWidget(btn)
        interval_layout.addLayout(days_layout)

        sched_layout.addWidget(self.interval_container)
        layout.addWidget(sched_frame)

        # Установка выбранного режима расписания
        mode_lower = (schedule_mode or "global").lower()
        if mode_lower == "24/7":
            self.rb_24_7.setChecked(True)
        elif mode_lower == "interval":
            self.rb_interval.setChecked(True)
        else:
            self.rb_global.setChecked(True)

        self._update_interval_visibility()
        self.mode_group.idToggled.connect(lambda: self._update_interval_visibility())

        # Сообщение об ошибке валидации
        self.error_label = QLabel("")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        layout.addSpacing(4)

        # Кнопки действий
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setObjectName("btnCancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_connect = QPushButton("Сохранить и подключиться")
        self.btn_connect.setObjectName("btnConnect")
        self.btn_connect.setDefault(True)
        self.btn_connect.clicked.connect(self._validate_and_accept)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_connect)
        layout.addLayout(btn_layout)

        # Фокус на поле ввода хоста
        self.host_input.setFocus()

    def _update_interval_visibility(self) -> None:
        """Показывает блок настройки интервала только при выбранном режиме 'Свой интервал'."""
        self.interval_container.setVisible(self.rb_interval.isChecked())

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

    def get_settings(self) -> SettingsResult:
        """
        Возвращает кортеж настроек SettingsResult.
        Поддерживает распаковку как в 3 переменных (host, port, path), так и доступ
        ко всем полям расписания.
        """
        host = self.host_input.text().strip()
        port = self.port_input.value()
        path = self.path_input.text().strip().lstrip("/") or "live"

        if self.rb_24_7.isChecked():
            sched_mode = "24/7"
        elif self.rb_interval.isChecked():
            sched_mode = "interval"
        else:
            sched_mode = "global"

        start_time = self.time_start.time().toString("HH:mm")
        end_time = self.time_end.time().toString("HH:mm")
        selected_days = [btn.property("day_num") for btn in self.day_buttons if btn.isChecked()]
        if not selected_days:
            selected_days = [1, 2, 3, 4, 5, 6, 7]

        return SettingsResult(
            host=host,
            port=port,
            path=path,
            schedule_mode=sched_mode,
            schedule_start=start_time,
            schedule_end=end_time,
            schedule_days=selected_days,
        )

    def get_schedule_settings(self) -> Tuple[str, str, str, List[int]]:
        """Возвращает параметры расписания (mode, start, end, days)."""
        res = self.get_settings()
        return res.schedule_mode, res.schedule_start, res.schedule_end, res.schedule_days

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
                background-color: #0f172a;
                color: #f1f5f9;
                border: 1px solid #334155;
                border-radius: 12px;
            }
            QLabel {
                color: #94a3b8;
                font-size: 13px;
            }
            #dialogTitle {
                color: #f8fafc;
                font-weight: 700;
            }
            #dialogSubtitle {
                color: #64748b;
                font-size: 12px;
            }
            #sectionHeader {
                color: #e2e8f0;
                font-weight: 600;
                font-size: 12px;
            }
            #subLabel {
                color: #94a3b8;
                font-size: 11px;
            }
            QLineEdit, QSpinBox, QTimeEdit {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 13px;
                selection-background-color: #2563eb;
            }
            QLineEdit:focus, QSpinBox:focus, QTimeEdit:focus {
                border: 1px solid #3b82f6;
                background-color: #0f172a;
            }
            #scheduleFrame {
                background-color: #1e293b/40;
                border: 1px solid #334155;
                border-radius: 8px;
            }
            QRadioButton {
                color: #cbd5e1;
                font-size: 12px;
                font-weight: 500;
                spacing: 6px;
            }
            QRadioButton:hover {
                color: #f8fafc;
            }
            QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border-radius: 7px;
                border: 1px solid #64748b;
                background-color: #1e293b;
            }
            QRadioButton::indicator:checked {
                border: 4px solid #3b82f6;
                background-color: #ffffff;
            }
            #dayBtn {
                background-color: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 5px 2px;
                font-weight: 600;
                font-size: 11px;
            }
            #dayBtn:hover {
                color: #f1f5f9;
                border-color: #475569;
            }
            #dayBtn:checked {
                background-color: #4f46e5;
                color: #ffffff;
                border: 1px solid #6366f1;
            }
            #btnLocalhost {
                background-color: #1e293b;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 6px;
                padding: 7px 12px;
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
                padding: 9px 18px;
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
                background-color: #334155;
                color: #e2e8f0;
                border: none;
                border-radius: 6px;
                padding: 9px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            #btnCancel:hover {
                background-color: #475569;
            }
            #errorLabel {
                color: #ef4444;
                font-size: 12px;
                font-weight: 500;
            }
        """)
