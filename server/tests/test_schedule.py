"""
Модульные и интеграционные тесты для системы управления расписанием вещания (24/7 и интервалы).
"""

import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.client_manager import ClientDevice, ClientManager
from core.config import ScheduleConfig, ServerSettings
from core.schedule_enforcer import ScheduleEnforcer
from main import (
    ClientScheduleUpdateRequest,
    ScheduleGlobalUpdateRequest,
    client_manager,
    config_manager,
    get_client_public_status,
    get_schedule_status,
    update_client_schedule_endpoint,
    update_global_schedule,
)


class TestScheduleLogic(unittest.TestCase):
    def test_schedule_config_defaults(self):
        """Проверка дефолтных значений модели ScheduleConfig."""
        config = ScheduleConfig()
        self.assertEqual(config.mode, "24/7")
        self.assertEqual(config.start_time, "08:00")
        self.assertEqual(config.end_time, "20:00")
        self.assertEqual(config.days_of_week, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(config.action_off, "standby")

    def test_client_device_schedule_defaults(self):
        """Проверка дефолтных полей расписания у ClientDevice."""
        client = ClientDevice(client_id="test-1", ip="192.168.1.10")
        self.assertEqual(client.schedule_mode, "global")
        self.assertEqual(client.schedule_start, "08:00")
        self.assertEqual(client.schedule_end, "20:00")
        self.assertEqual(client.schedule_days, [1, 2, 3, 4, 5, 6, 7])
        self.assertFalse(client.standby_by_schedule)

    def test_is_in_schedule_window_24_7(self):
        """В режиме 24/7 устройство всегда должно быть активно в любое время."""
        client = ClientDevice(client_id="test-1", ip="192.168.1.10", schedule_mode="24/7")
        dt_midnight = datetime.datetime(2026, 9, 5, 3, 0, 0)
        dt_noon = datetime.datetime(2026, 9, 5, 12, 0, 0)
        dt_evening = datetime.datetime(2026, 9, 5, 23, 59, 0)

        self.assertTrue(client.is_in_schedule_window(now_dt=dt_midnight))
        self.assertTrue(client.is_in_schedule_window(now_dt=dt_noon))
        self.assertTrue(client.is_in_schedule_window(now_dt=dt_evening))

    def test_is_in_schedule_window_daytime_interval(self):
        """Проверка дневного интервала: 08:00 - 20:00."""
        client = ClientDevice(
            client_id="test-1",
            ip="192.168.1.10",
            schedule_mode="interval",
            schedule_start="08:00",
            schedule_end="20:00",
            schedule_days=[1, 2, 3, 4, 5, 6, 7],
        )

        # 07:59 -> Вне окна
        dt_before = datetime.datetime(2026, 9, 5, 7, 59, 59)
        self.assertFalse(client.is_in_schedule_window(now_dt=dt_before))

        # 08:00 -> В окне
        dt_start = datetime.datetime(2026, 9, 5, 8, 0, 0)
        self.assertTrue(client.is_in_schedule_window(now_dt=dt_start))

        # 14:30 -> В окне
        dt_middle = datetime.datetime(2026, 9, 5, 14, 30, 0)
        self.assertTrue(client.is_in_schedule_window(now_dt=dt_middle))

        # 20:00 -> Вне окна (граница окончания)
        dt_end = datetime.datetime(2026, 9, 5, 20, 0, 0)
        self.assertFalse(client.is_in_schedule_window(now_dt=dt_end))

        # 22:15 -> Вне окна
        dt_after = datetime.datetime(2026, 9, 5, 22, 15, 0)
        self.assertFalse(client.is_in_schedule_window(now_dt=dt_after))

    def test_is_in_schedule_window_overnight_interval(self):
        """Проверка ночного интервала с переходом через полночь: 22:00 - 06:00."""
        client = ClientDevice(
            client_id="test-1",
            ip="192.168.1.10",
            schedule_mode="interval",
            schedule_start="22:00",
            schedule_end="06:00",
            schedule_days=[1, 2, 3, 4, 5, 6, 7],
        )

        # 21:59 -> Вне окна
        self.assertFalse(client.is_in_schedule_window(now_dt=datetime.datetime(2026, 9, 5, 21, 59, 0)))

        # 22:00 -> В окне
        self.assertTrue(client.is_in_schedule_window(now_dt=datetime.datetime(2026, 9, 5, 22, 0, 0)))

        # 23:45 -> В окне
        self.assertTrue(client.is_in_schedule_window(now_dt=datetime.datetime(2026, 9, 5, 23, 45, 0)))

        # 03:00 -> В окне
        self.assertTrue(client.is_in_schedule_window(now_dt=datetime.datetime(2026, 9, 6, 3, 0, 0)))

        # 05:59 -> В окне
        self.assertTrue(client.is_in_schedule_window(now_dt=datetime.datetime(2026, 9, 6, 5, 59, 0)))

        # 06:00 -> Вне окна
        self.assertFalse(client.is_in_schedule_window(now_dt=datetime.datetime(2026, 9, 6, 6, 0, 0)))

        # 12:00 -> Вне окна
        self.assertFalse(client.is_in_schedule_window(now_dt=datetime.datetime(2026, 9, 6, 12, 0, 0)))

    def test_is_in_schedule_window_days_of_week(self):
        """Проверка фильтрации по дням недели (например, только будни Пн-Пт: 1..5)."""
        client = ClientDevice(
            client_id="test-1",
            ip="192.168.1.10",
            schedule_mode="interval",
            schedule_start="08:00",
            schedule_end="20:00",
            schedule_days=[1, 2, 3, 4, 5],  # Пн-Пт
        )

        # 2026-09-04 — Пятница (weekday = 5)
        dt_friday = datetime.datetime(2026, 9, 4, 12, 0, 0)
        self.assertEqual(dt_friday.isoweekday(), 5)
        self.assertTrue(client.is_in_schedule_window(now_dt=dt_friday))

        # 2026-09-05 — Суббота (weekday = 6)
        dt_saturday = datetime.datetime(2026, 9, 5, 12, 0, 0)
        self.assertEqual(dt_saturday.isoweekday(), 6)
        self.assertFalse(client.is_in_schedule_window(now_dt=dt_saturday))

    def test_global_schedule_inheritance_and_override(self):
        """Проверка наследования общего серверного расписания и переопределения."""
        global_sched = ScheduleConfig(
            mode="interval",
            start_time="09:00",
            end_time="18:00",
            days_of_week=[1, 2, 3, 4, 5],
        )

        # Клиент с режимом 'global' подчиняется общему расписанию
        client_global = ClientDevice(client_id="c-glob", ip="10.0.0.1", schedule_mode="global")
        # В 12:00 в пятницу -> True
        self.assertTrue(client_global.is_in_schedule_window(datetime.datetime(2026, 9, 4, 12, 0, 0), global_sched))
        # В 21:00 в пятницу -> False
        self.assertFalse(client_global.is_in_schedule_window(datetime.datetime(2026, 9, 4, 21, 0, 0), global_sched))

        # Клиент с переопределением на '24/7' всегда активен, несмотря на глобальный интервал
        client_247 = ClientDevice(client_id="c-247", ip="10.0.0.2", schedule_mode="24/7")
        self.assertTrue(client_247.is_in_schedule_window(datetime.datetime(2026, 9, 4, 21, 0, 0), global_sched))

        # Клиент с собственным интервалом (например, вечерний бар с 19:00 до 23:00)
        client_custom = ClientDevice(
            client_id="c-custom",
            ip="10.0.0.3",
            schedule_mode="interval",
            schedule_start="19:00",
            schedule_end="23:00",
            schedule_days=[1, 2, 3, 4, 5, 6, 7],
        )
        # В 12:00 (глобальное активно, а персональное еще нет) -> False
        self.assertFalse(client_custom.is_in_schedule_window(datetime.datetime(2026, 9, 4, 12, 0, 0), global_sched))
        # В 20:00 (глобальное уже выключено, а персональное активно) -> True
        self.assertTrue(client_custom.is_in_schedule_window(datetime.datetime(2026, 9, 4, 20, 0, 0), global_sched))


class TestScheduleEnforcer(unittest.IsolatedAsyncioTestCase):
    async def test_enforcer_standby_activation_and_preservation(self):
        """
        Проверка логики ScheduleEnforcer:
        1. Вне расписания переводит в standby=True с пометкой standby_by_schedule=True.
        2. При наступлении времени вещания возвращает в standby=False.
        3. Не сбрасывает ручной standby администратора (когда standby_by_schedule=False).
        """
        from pathlib import Path
        mgr = ClientManager(storage_path=Path("test_clients_tmp.json"))
        c1 = await mgr.register_or_update(
            client_id="dev-1",
            ip="192.168.1.101",
            data={
                "hostname": "tv1",
                "screens": ["Screen 1"],
                "primary_screen": "Screen 1",
            },
        )
        await mgr.update_client_schedule(
            client_id="dev-1",
            schedule_mode="interval",
            schedule_start="08:00",
            schedule_end="18:00",
            schedule_days=[1, 2, 3, 4, 5, 6, 7],
        )

        mock_config_mgr = MagicMock()
        mock_config_mgr.get_settings.return_value = ServerSettings(schedule=ScheduleConfig(mode="24/7"))

        enforcer = ScheduleEnforcer(client_manager=mgr, config_manager=mock_config_mgr)

        try:
            # 1. Время 21:00 (вне интервала 08:00-18:00)
            night_time = datetime.datetime(2026, 9, 5, 21, 0, 0)
            await enforcer.enforce_once(now_dt=night_time)

            dev_after_night = mgr.get_client("dev-1")
            self.assertTrue(dev_after_night.standby)
            self.assertTrue(dev_after_night.standby_by_schedule)

            # 2. Наступило утро 10:00 (в интервале)
            day_time = datetime.datetime(2026, 9, 6, 10, 0, 0)
            await enforcer.enforce_once(now_dt=day_time)

            dev_after_morning = mgr.get_client("dev-1")
            self.assertFalse(dev_after_morning.standby)
            self.assertFalse(dev_after_morning.standby_by_schedule)

            # 3. Администратор вручную выключил устройство днем
            await mgr.set_standby("dev-1", standby=True)
            dev_manual = mgr.get_client("dev-1")
            self.assertTrue(dev_manual.standby)
            self.assertFalse(dev_manual.standby_by_schedule)

            # Прогон планировщика в дневное время не должен будить устройство, выключенное вручную
            await enforcer.enforce_once(now_dt=day_time)
            dev_still_manual = mgr.get_client("dev-1")
            self.assertTrue(dev_still_manual.standby)
            self.assertFalse(dev_still_manual.standby_by_schedule)
        finally:
            tmp_p = Path("test_clients_tmp.json")
            if tmp_p.exists():
                tmp_p.unlink(missing_ok=True)


class TestScheduleAPIEndpoints(unittest.IsolatedAsyncioTestCase):
    async def test_get_and_update_schedule_api(self):
        """Проверка эндпоинтов GET и POST /api/schedule."""
        status = await get_schedule_status()
        self.assertIn("mode", status)
        self.assertIn("is_active_now", status)
        self.assertIn("server_time", status)

        # Обновление глобального расписания
        update_req = ScheduleGlobalUpdateRequest(
            mode="interval",
            start_time="07:55",
            end_time="19:55",
            days_of_week=[1, 2, 3, 4, 5, 6, 7],
            action_off="standby",
        )
        res = await update_global_schedule(update_req)
        self.assertTrue(res.get("success"))
        self.assertEqual(res["schedule"]["mode"], "interval")
        self.assertEqual(res["schedule"]["start_time"], "07:55")
        self.assertEqual(res["schedule"]["end_time"], "19:55")

    async def test_update_client_schedule_and_public_status(self):
        """Проверка настройки расписания клиента и опроса через публичный эндпоинт."""
        # Регистрируем тестового клиента
        c = await client_manager.register_or_update(
            client_id="schedule-test-tv",
            ip="192.168.1.188",
            data={
                "hostname": "test-tv",
                "screens": ["TV-Screen"],
                "primary_screen": "TV-Screen",
            },
        )

        req = ClientScheduleUpdateRequest(
            client_id="schedule-test-tv",
            schedule_mode="interval",
            schedule_start="09:00",
            schedule_end="21:00",
            schedule_days=[1, 2, 3, 4, 5],
        )
        res = await update_client_schedule_endpoint(req)
        self.assertTrue(res.get("success"))

        # Публичный эндпоинт для Android ТВ
        dummy_request = MagicMock()
        dummy_request.client.host = "192.168.1.188"

        status = await get_client_public_status(
            request=dummy_request,
            client_id="schedule-test-tv",
            ip="192.168.1.188",
        )
        self.assertEqual(status["client_id"], "schedule-test-tv")
        self.assertEqual(status["schedule_mode"], "interval")
        self.assertEqual(status["schedule_start"], "09:00")
        self.assertEqual(status["schedule_end"], "21:00")
        self.assertIn("is_in_schedule", status)
        self.assertIn("standby", status)


if __name__ == "__main__":
    unittest.main()
