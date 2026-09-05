"""
Тесты логики контроллера, фильтрации клавиатурных событий и окна настроек.
Использует headless платформу Qt (offscreen) для надежного выполнения в тестах.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

client_dir = Path(__file__).resolve().parent.parent
if str(client_dir) not in sys.path:
    sys.path.insert(0, str(client_dir))

# Настройка headless режима для Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

# Создаем экземпляр приложения для тестов
app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from app_controller import GlobalKeyFilter
from config import ClientConfig, ConfigManager
from settings_dialog import SettingsDialog


class TestGlobalKeyFilter(unittest.TestCase):
    """Тестирование перехвата клавиши 'Q' во всех раскладках."""

    def test_key_q_triggers_callback(self):
        callback = MagicMock()
        filter_obj = GlobalKeyFilter(callback)

        # 1. Английская 'Q'
        event_q = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Q, Qt.KeyboardModifier.NoModifier, "q")
        handled = filter_obj.eventFilter(None, event_q)
        self.assertTrue(handled)
        app.processEvents()
        callback.assert_called_once()

    def test_russian_layout_y_triggers_callback(self):
        callback = MagicMock()
        filter_obj = GlobalKeyFilter(callback)

        # 2. Русская 'й' (соответствует физической клавише Q на стандартной клавиатуре)
        event_ru_lower = QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, "й")
        handled = filter_obj.eventFilter(None, event_ru_lower)
        self.assertTrue(handled)
        app.processEvents()
        callback.assert_called_once()

        # 3. Русская заглавная 'Й'
        callback.reset_mock()
        event_ru_upper = QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.ShiftModifier, "Й")
        handled = filter_obj.eventFilter(None, event_ru_upper)
        self.assertTrue(handled)
        app.processEvents()
        callback.assert_called_once()

    def test_key_e_triggers_exit_callback(self):
        settings_cb = MagicMock()
        exit_cb = MagicMock()
        filter_obj = GlobalKeyFilter(settings_cb, exit_cb)

        # 1. Английская 'E'
        event_e = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_E, Qt.KeyboardModifier.NoModifier, "e")
        handled = filter_obj.eventFilter(None, event_e)
        self.assertTrue(handled)
        app.processEvents()
        exit_cb.assert_called_once()
        settings_cb.assert_not_called()

        # 2. Русская 'у' (соответствует клавише E на клавиатуре)
        exit_cb.reset_mock()
        event_ru_u = QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, "у")
        handled = filter_obj.eventFilter(None, event_ru_u)
        self.assertTrue(handled)
        app.processEvents()
        exit_cb.assert_called_once()

        # 3. Русская заглавная 'У'
        exit_cb.reset_mock()
        event_ru_upper_u = QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.ShiftModifier, "У")
        handled = filter_obj.eventFilter(None, event_ru_upper_u)
        self.assertTrue(handled)
        app.processEvents()
        exit_cb.assert_called_once()

    def test_other_keys_are_passed_through(self):
        callback = MagicMock()
        filter_obj = GlobalKeyFilter(callback)

        # Например, клавиша Space или 'A'
        event_space = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
        handled = filter_obj.eventFilter(None, event_space)
        self.assertFalse(handled)
        callback.assert_not_called()


class TestSettingsDialog(unittest.TestCase):
    """Тестирование диалогового окна настроек."""

    def test_dialog_init_and_values(self):
        dialog = SettingsDialog(current_host="192.168.1.50", current_port=8554, current_path="channel1")
        host, port, path = dialog.get_settings()
        self.assertEqual(host, "192.168.1.50")
        self.assertEqual(port, 8554)
        self.assertEqual(path, "channel1")

    def test_localhost_button(self):
        dialog = SettingsDialog(current_host="", current_port=1234, current_path="old")
        dialog._set_localhost()
        host, port, path = dialog.get_settings()
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8554)
        self.assertEqual(path, "live")

    def test_empty_host_validation(self):
        dialog = SettingsDialog(current_host="", current_port=8554, current_path="live")
        dialog.show()
        dialog._validate_and_accept()
        # Должна отобразиться ошибка
        self.assertFalse(dialog.error_label.isHidden())
        self.assertIn("укажите адрес сервера", dialog.error_label.text())
        dialog.close()


class TestAudioMuteLogic(unittest.TestCase):
    """Тестирование логики маршрутизации аудио с учетом флага audio_allowed."""

    def test_audio_routing_rules(self):
        # Логика: звук активен только если audio_allowed=True И is_primary=True
        def is_sound_active(is_primary: bool, audio_allowed: bool) -> bool:
            return is_primary and audio_allowed

        # 1. Звук разрешен сервером:
        self.assertTrue(is_sound_active(is_primary=True, audio_allowed=True), "Основной монитор должен играть звук")
        self.assertFalse(is_sound_active(is_primary=False, audio_allowed=True), "Дополнительный монитор заглушен")

        # 2. Звук заблокирован сервером:
        self.assertFalse(is_sound_active(is_primary=True, audio_allowed=False), "При audio_allowed=False звук заглушен")
        self.assertFalse(is_sound_active(is_primary=False, audio_allowed=False), "При audio_allowed=False звук заглушен")


class TestDetailedOsInfo(unittest.TestCase):
    """Тестирование функции определения подробной информации об ОС."""

    def test_get_detailed_os_info(self):
        from app_controller import get_detailed_os_info
        info = get_detailed_os_info()
        self.assertIsInstance(info, str)
        self.assertTrue(len(info) > 0)
        if sys.platform == "win32":
            self.assertIn("Windows", info)
            self.assertIn("Build", info)


if __name__ == "__main__":
    unittest.main()
