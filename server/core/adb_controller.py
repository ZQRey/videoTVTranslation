"""
Модуль сетевого управления Android-устройствами по протоколу ADB (Android Debug Bridge).
Обеспечивает обнаружение бинарника ADB (в комплекте сервера, в Docker /usr/bin/adb или на хосте),
подключение к Android ТВ-приставкам и телевизорам по TCP/IP (порт 5555) и выполнение
команд управления питанием:
- reboot -p / svc power shutdown (активное выключение)
- input keyevent 26 (сон / гашение экрана)
- input keyevent 224 (пробуждение экрана)
- reboot (перезагрузка)
- getprop (опрос модели и версии Android)
"""

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("stream_server.adb")


class AdbController:
    """Контроллер управления устройствами Android по сети через ADB."""

    def __init__(self, custom_adb_path: Optional[str] = None):
        self._custom_adb_path = custom_adb_path
        self._cached_path: Optional[Path] = None

    def find_adb_binary(self) -> Optional[Path]:
        """
        Поиск исполняемого файла adb в порядке приоритета:
        1. Пользовательский путь (если задан)
        2. Комплектные бинарники проекта (server/bin/adb/windows/ или server/bin/adb/linux/)
        3. Системный PATH (включая установленный в Docker /usr/bin/adb)
        4. Переменные окружения ANDROID_HOME / ANDROID_SDK_ROOT
        5. Стандартные пути установки на Windows (%LOCALAPPDATA%\\Android\\Sdk\\platform-tools\\adb.exe)
        6. Стандартные пути Linux/macOS (/usr/bin/adb, /usr/local/bin/adb)
        """
        if self._cached_path and self._cached_path.is_file():
            return self._cached_path

        # 1. Custom path
        if self._custom_adb_path:
            p = Path(self._custom_adb_path)
            if p.is_file():
                self._cached_path = p
                return p

        # 2. Комплектная директория проекта server/bin/adb/
        base_dir = Path(__file__).resolve().parent.parent  # server/
        if sys.platform == "win32":
            bundled_win = base_dir / "bin" / "adb" / "windows" / "adb.exe"
            if bundled_win.is_file():
                self._cached_path = bundled_win
                return bundled_win
        else:
            bundled_linux = base_dir / "bin" / "adb" / "linux" / "adb"
            if bundled_linux.is_file():
                self._cached_path = bundled_linux
                return bundled_linux

        # 3. Системный PATH (Docker /usr/bin/adb или хост)
        which_name = "adb.exe" if sys.platform == "win32" else "adb"
        in_path = shutil.which(which_name) or shutil.which("adb")
        if in_path:
            p = Path(in_path)
            if p.is_file():
                self._cached_path = p
                return p

        # 4. Переменные ANDROID_HOME / ANDROID_SDK_ROOT
        for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
            sdk_dir = os.environ.get(env_var)
            if sdk_dir:
                p = Path(sdk_dir) / "platform-tools" / which_name
                if p.is_file():
                    self._cached_path = p
                    return p

        # 5. Стандартные каталоги Windows
        if sys.platform == "win32":
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            if local_appdata:
                p = Path(local_appdata) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
                if p.is_file():
                    self._cached_path = p
                    return p

            program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
            for pf_candidate in (
                Path(program_files) / "Android" / "platform-tools" / "adb.exe",
                Path("C:\\Android\\platform-tools\\adb.exe"),
            ):
                if pf_candidate.is_file():
                    self._cached_path = pf_candidate
                    return pf_candidate

        # 6. Стандартные каталоги Linux/macOS
        for unix_path in (Path("/usr/bin/adb"), Path("/usr/local/bin/adb")):
            if unix_path.is_file():
                self._cached_path = unix_path
                return unix_path

        return None

    def is_available(self) -> bool:
        """Возвращает True, если утилита ADB обнаружена и доступна для запуска."""
        return self.find_adb_binary() is not None

    def get_adb_path(self) -> Optional[str]:
        """Возвращает строковый путь к обнаруженному исполняемому файлу ADB."""
        p = self.find_adb_binary()
        return str(p) if p else None

    async def run_adb_raw(self, args: List[str], timeout: float = 10.0) -> Tuple[int, str, str]:
        """
        Асинхронный запуск утилиты adb с заданными аргументами.
        Возвращает (exit_code, stdout, stderr).
        """
        adb_bin = self.find_adb_binary()
        if not adb_bin:
            raise FileNotFoundError("Утилита ADB не найдена на сервере")

        cmd = [str(adb_bin)] + args
        logger.debug("ADB Executing: %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                str(adb_bin),
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout = stdout_b.decode("utf-8", errors="replace").strip()
            stderr = stderr_b.decode("utf-8", errors="replace").strip()
            return proc.returncode if proc.returncode is not None else -1, stdout, stderr
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return -1, "", f"Таймаут выполнения команды ADB ({timeout} с)"
        except Exception as e:
            return -1, "", str(e)

    async def connect_device(self, ip: str, port: int = 5555, timeout: float = 6.0) -> Tuple[bool, str]:
        """
        Подключение к Android-устройству по сети: adb connect <ip>:<port>.
        """
        target = f"{ip}:{port}"
        code, out, err = await self.run_adb_raw(["connect", target], timeout=timeout)
        combined = f"{out} {err}".strip().lower()

        if "connected to" in combined or "already connected to" in combined:
            logger.info("ADB успешно подключен к %s (%s)", target, out)
            return True, out or "Подключено"

        msg = err if err else (out if out else f"Не удалось подключиться к {target}")
        logger.warning("Ошибка подключения ADB к %s: %s", target, msg)
        return False, msg

    async def execute_action(
        self,
        ip: str,
        port: int = 5555,
        action: str = "shutdown",
        custom_cmd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Выполнение действия управления на Android-устройстве.
        Поддерживаемые действия:
        - 'shutdown' / 'poweroff': выключение устройства (reboot -p / svc power shutdown)
        - 'sleep': перевод экрана в режим сна (input keyevent 26)
        - 'wakeup': пробуждение экрана (input keyevent 224)
        - 'reboot': перезагрузка устройства (reboot)
        - 'get_info': опрос информации о модели и версии Android
        - 'custom': выполнение пользовательской команды shell
        """
        if not self.is_available():
            return {
                "success": False,
                "message": "Утилита ADB не найдена на сервере. Убедитесь, что adb установлен.",
                "action": action,
                "output": "",
            }

        target = f"{ip}:{port}"
        # 1. Подключаемся к устройству
        connected, conn_msg = await self.connect_device(ip, port)
        if not connected:
            return {
                "success": False,
                "message": f"Ошибка сетевого подключения ADB к {target}: {conn_msg}",
                "action": action,
                "output": conn_msg,
            }

        # 2. Выполнение целевого действия
        if action in ("shutdown", "poweroff"):
            # Попытка 1: reboot -p (стандартный shutdown)
            code, out, err = await self.run_adb_raw(["-s", target, "shell", "reboot", "-p"], timeout=8.0)
            if code == 0 or "rebooting" in out.lower():
                return {
                    "success": True,
                    "message": f"Команда выключения (reboot -p) отправлена на {target}",
                    "action": "shutdown",
                    "output": out or "OK",
                }

            # Попытка 2: svc power shutdown
            code2, out2, err2 = await self.run_adb_raw(["-s", target, "shell", "svc", "power", "shutdown"], timeout=8.0)
            if code2 == 0:
                return {
                    "success": True,
                    "message": f"Команда выключения (svc power shutdown) отправлена на {target}",
                    "action": "shutdown",
                    "output": out2 or "OK",
                }

            # Попытка 3: перевод экрана в сон через Power key (для устройств с заблокированным shutdown)
            await self.run_adb_raw(["-s", target, "shell", "input", "keyevent", "26"], timeout=5.0)
            return {
                "success": True,
                "message": f"Экран {target} выключен (нажата клавиша Power). Полное выключение заблокировано прошивкой.",
                "action": "shutdown",
                "output": f"reboot error: {err}; svc error: {err2}",
            }

        elif action == "sleep":
            code, out, err = await self.run_adb_raw(["-s", target, "shell", "input", "keyevent", "26"], timeout=6.0)
            return {
                "success": code == 0,
                "message": f"Экран {target} переведен в спящий режим" if code == 0 else f"Ошибка перевода в сон: {err}",
                "action": "sleep",
                "output": out or err,
            }

        elif action == "wakeup":
            code, out, err = await self.run_adb_raw(["-s", target, "shell", "input", "keyevent", "224"], timeout=6.0)
            return {
                "success": code == 0,
                "message": f"Экран {target} пробужден" if code == 0 else f"Ошибка пробуждения: {err}",
                "action": "wakeup",
                "output": out or err,
            }

        elif action == "reboot":
            code, out, err = await self.run_adb_raw(["-s", target, "shell", "reboot"], timeout=8.0)
            return {
                "success": code == 0,
                "message": f"Команда перезагрузки отправлена на {target}",
                "action": "reboot",
                "output": out or err,
            }

        elif action == "get_info":
            # Опрос модели и версии системы
            script = "getprop ro.product.model; echo '---'; getprop ro.build.version.release; echo '---'; getprop ro.build.version.sdk"
            code, out, err = await self.run_adb_raw(["-s", target, "shell", script], timeout=7.0)
            if code != 0:
                return {
                    "success": False,
                    "message": f"Не удалось опросить устройство {target}: {err}",
                    "action": "get_info",
                    "output": err,
                }

            parts = [p.strip() for p in out.split("---")]
            model = parts[0] if len(parts) > 0 else "Android Device"
            release = parts[1] if len(parts) > 1 else ""
            sdk = parts[2] if len(parts) > 2 else ""

            formatted = f"Android {release}" if release else "Android"
            if sdk:
                formatted += f" (API {sdk})"
            if model:
                formatted += f", {model}"

            return {
                "success": True,
                "message": f"Получена информация об устройстве: {formatted}",
                "action": "get_info",
                "model": model,
                "android_version": release,
                "sdk": sdk,
                "formatted_os": formatted,
                "output": out,
            }

        elif action == "custom":
            if not custom_cmd or not custom_cmd.strip():
                return {
                    "success": False,
                    "message": "Пользовательская команда shell не указана",
                    "action": "custom",
                    "output": "",
                }
            code, out, err = await self.run_adb_raw(["-s", target, "shell", custom_cmd.strip()], timeout=12.0)
            return {
                "success": code == 0,
                "message": f"Команда выполнена с кодом {code}",
                "action": "custom",
                "output": out if code == 0 else f"{out}\n{err}".strip(),
            }

        else:
            return {
                "success": False,
                "message": f"Неизвестное действие: '{action}'",
                "action": action,
                "output": "",
            }


# Глобальный синглтон контроллера ADB
adb_controller = AdbController()
