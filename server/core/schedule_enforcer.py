"""
Сервис серверного контроля расписания вещания (Schedule Enforcer).
Периодически проверяет соответствие текущего времени суточному графику трансляции
для всех клиентов (глобально) и для каждого устройства индивидуально.
Переводит клиентские плееры в режим ожидания (чёрный экран, mute) вне графика
и автоматически возобновляет вещание при наступлении разрешенного времени.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from core.adb_controller import adb_controller
from core.client_manager import ClientManager, client_manager
from core.config import ConfigManager, config_manager

logger = logging.getLogger("stream_server.schedule")


class ScheduleEnforcer:
    """
    Фоновый исполнитель контроля расписания вещания.
    """

    def __init__(
        self,
        cfg_mgr: Optional[ConfigManager] = None,
        cl_mgr: Optional[ClientManager] = None,
        check_interval_sec: float = 3.0,
        config_manager: Optional[ConfigManager] = None,
        client_manager: Optional[ClientManager] = None,
    ):
        from core.config import config_manager as default_cfg
        from core.client_manager import client_manager as default_cl
        self.config_manager = config_manager or cfg_mgr or default_cfg
        self.client_manager = client_manager or cl_mgr or default_cl
        self.check_interval_sec = check_interval_sec
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        """Запуск фонового сервиса проверки расписания."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Сервис контроля расписания вещания (Schedule Enforcer) успешно запущен.")

    async def stop(self) -> None:
        """Корректная остановка сервиса."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Сервис контроля расписания вещания остановлен.")

    async def _run_loop(self) -> None:
        """Основной цикл периодической сверки времени и состояния клиентов."""
        while self._running:
            try:
                await self.check_and_enforce_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Ошибка в цикле контроля расписания: %s", e)

            try:
                await asyncio.sleep(self.check_interval_sec)
            except asyncio.CancelledError:
                break

    async def enforce_once(self, now_dt: Optional[datetime] = None) -> None:
        """Однократное принудительное выполнение проверки расписания (для тестов)."""
        await self.check_and_enforce_all(now_dt=now_dt)

    async def check_and_enforce_all(self, now_dt: Optional[datetime] = None) -> None:
        """
        Проверка всех клиентов и применение необходимых изменений (включение/чёрный экран).
        """
        if now_dt is None:
            now_dt = datetime.now()

        settings = self.config_manager.get_settings()
        global_schedule = getattr(settings, "schedule", None)

        clients = list(self.client_manager._clients.values())
        for client in clients:
            try:
                should_be_active = client.is_in_schedule_window(
                    now_dt=now_dt,
                    global_schedule=global_schedule,
                )

                if not should_be_active:
                    # Клиент находится вне окна вещания -> должен показывать чёрный экран
                    if not client.standby:
                        client.standby_by_schedule = True
                        await self.client_manager.set_standby(client.client_id, True)
                        logger.info(
                            "🌙 [Расписание] Клиент [%s] (%s) переведен в режим ожидания (чёрный экран). Режим: %s",
                            client.client_id,
                            client.custom_name,
                            client.schedule_mode,
                        )
                        # Если это Android-устройство и задано действие ADB сна
                        if client.os_family == "android" and client.ip and global_schedule and getattr(global_schedule, "action_off", "standby") == "adb_sleep":
                            asyncio.create_task(self._safe_adb_action(client.ip, "sleep"))
                else:
                    # Клиент находится в окне вещания -> должен вещать
                    if client.standby and client.standby_by_schedule:
                        # Восстанавливаем только если режим ожидания был активирован расписанием
                        client.standby_by_schedule = False
                        await self.client_manager.set_standby(client.client_id, False)
                        logger.info(
                            "☀️ [Расписание] Клиент [%s] (%s) возобновил вещание по расписанию.",
                            client.client_id,
                            client.custom_name,
                        )
                        if client.os_family == "android" and client.ip and global_schedule and getattr(global_schedule, "action_off", "standby") == "adb_sleep":
                            asyncio.create_task(self._safe_adb_action(client.ip, "wakeup"))
            except Exception as ex:
                logger.debug("Ошибка при обработке расписания для клиента [%s]: %s", client.client_id, ex)

    async def _safe_adb_action(self, ip: str, action: str) -> None:
        """Безопасный вызов ADB команды без блокировки основного цикла."""
        try:
            await adb_controller.execute_action(ip=ip, port=5555, action=action)
        except Exception as e:
            logger.debug("Не удалось выполнить ADB действие '%s' для %s: %s", action, ip, e)

    def get_global_status(self) -> Dict[str, Any]:
        """Получение текущей информации о глобальном расписании и статусе эфира."""
        settings = self.config_manager.get_settings()
        sched = getattr(settings, "schedule", None)
        now = datetime.now()

        mode = getattr(sched, "mode", "24/7") if sched else "24/7"
        start_time = getattr(sched, "start_time", "08:00") if sched else "08:00"
        end_time = getattr(sched, "end_time", "20:00") if sched else "20:00"
        days = getattr(sched, "days_of_week", [1, 2, 3, 4, 5, 6, 7]) if sched else [1, 2, 3, 4, 5, 6, 7]
        action_off = getattr(sched, "action_off", "standby") if sched else "standby"

        # Проверяем, активен ли глобальный эфир прямо сейчас
        if mode == "24/7":
            is_active_now = True
        else:
            weekday = now.isoweekday()
            if weekday not in (days or [1, 2, 3, 4, 5, 6, 7]):
                is_active_now = False
            else:
                from core.client_manager import ClientDevice
                is_active_now = ClientDevice._is_time_in_range(now.time(), start_time, end_time)

        return {
            "mode": mode,
            "start_time": start_time,
            "end_time": end_time,
            "days_of_week": days,
            "action_off": action_off,
            "is_active_now": is_active_now,
            "server_time": now.strftime("%H:%M:%S"),
            "server_date": now.strftime("%Y-%m-%d"),
            "server_weekday": now.isoweekday(),
        }


# Глобальный синглтон службы контроля расписания
schedule_enforcer = ScheduleEnforcer()
