"""
Главный модуль приложения FastAPI.
Предоставляет REST API для управления вещанием, WebSocket для логов,
отдает статические файлы и веб-панель управления.
"""

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.auth import (
    SESSION_COOKIE_NAME,
    LdapAuthService,
    PasswordHasher,
    UserSession,
    get_current_user_optional,
    require_authenticated_user,
    session_manager,
)
from core.adb_controller import adb_controller
from core.client_manager import client_manager
from core.config import ServerSettings, config_manager
from core.logger import log_broadcaster, setup_logging
from core.playlist import PlaylistManager
from core.plugins.manager import STARTER_PYTHON_PLUGIN_TEMPLATE, PluginManager
from core.scanner import MediaScanner
from core.schedule_enforcer import schedule_enforcer
from core.streamer import StreamOrchestrator

# Инициализация логирования
logger = setup_logging()

# Инициализация ключевых компонентов ядра
playlist_mgr = PlaylistManager()
plugin_mgr = PluginManager(config_manager)
streamer = StreamOrchestrator(config_manager, playlist_mgr, plugin_mgr)
scanner = MediaScanner(config_manager, playlist_mgr)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Жизненный цикл FastAPI: запуск и корректное завершение фоновых задач.
    """
    logger.info("Запуск сервисов медиасервера...")
    log_broadcaster.start()
    scanner.start()
    streamer.start()
    schedule_enforcer.start()

    yield

    logger.info("Завершение работы сервисов медиасервера...")
    await schedule_enforcer.stop()
    await streamer.stop(keep_black_alive=False)
    await scanner.stop()
    await log_broadcaster.stop()
    logger.info("Все сервисы штатно остановлены.")


app = FastAPI(
    title="Continuous Broadcast Stream Server",
    description="Медиасервер непрерывного вещания с веб-панелью и плагинами",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы и шаблоны
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# -------------------------------------------------------------
# Middleware авторизации
# -------------------------------------------------------------


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """
    Middleware проверки сессии для веб-панели и REST API.
    Публичные маршруты: /login, /api/auth/login, /static/*, /favicon.ico.
    Для остальных:
      - при обращении к / перенаправляет на /login
      - при обращении к /api/* возвращает 401 Unauthorized
    """
    path = request.url.path
    if (
        path == "/login"
        or path == "/api/auth/login"
        or path.startswith("/static/")
        or path == "/favicon.ico"
        or path == "/api/client/status"
    ):
        return await call_next(request)

    user = get_current_user_optional(request)
    if not user:
        if path == "/" or not path.startswith("/api/"):
            return RedirectResponse(url="/login", status_code=302)
        return JSONResponse(
            status_code=401,
            content={"detail": "Требуется авторизация в системе"},
        )

    return await call_next(request)


# -------------------------------------------------------------
# Модели запросов авторизации
# -------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str
    auth_type: str = "local"  # "local" | "domain"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# -------------------------------------------------------------
# Эндпоинты веб-интерфейса и авторизации
# -------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def serve_login(request: Request):
    """Страница входа в систему."""
    user = get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Главная страница веб-панели управления."""
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/auth/login")
async def api_login(req: LoginRequest):
    """Вход в систему через локальную учетную запись или Active Directory."""
    username = req.username.strip()
    password = req.password
    auth_type = req.auth_type.lower()
    settings = config_manager.get_settings().auth

    if auth_type == "local":
        if not settings.local.enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Локальная авторизация отключена в настройках сервера",
            )
        if username != settings.local.username or not PasswordHasher.verify_password(
            password, settings.local.password_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверное имя пользователя или пароль",
            )
        display_name = username
    elif auth_type == "domain":
        if not settings.domain.enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Авторизация через Active Directory отключена в настройках",
            )
        success, message, user_info = LdapAuthService.authenticate(username, password)
        if not success:
            if "Администраторам" in message or "запрещен" in message:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)
        display_name = user_info["display_name"] if user_info else username
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неизвестный тип авторизации: {auth_type}",
        )

    session = session_manager.create_session(
        username=username,
        display_name=display_name,
        auth_type=auth_type,
        is_admin=True,
        lifetime_hours=settings.session_lifetime_hours,
    )

    response = JSONResponse(
        {
            "success": True,
            "message": "Успешная авторизация",
            "user": {
                "username": session.username,
                "display_name": session.display_name,
                "auth_type": session.auth_type,
                "is_admin": session.is_admin,
            },
        }
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.session_id,
        httponly=True,
        samesite="lax",
        max_age=settings.session_lifetime_hours * 3600,
    )
    return response


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    """Выход из системы и удаление пользовательской сессии."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_id = auth_header.split(" ", 1)[1].strip()

    if session_id:
        session_manager.delete_session(session_id)

    response = JSONResponse({"success": True, "message": "Сессия успешно завершена"})
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response


@app.get("/api/auth/me")
async def api_get_current_user(request: Request):
    """Получение информации о текущем авторизованном пользователе."""
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")
    return {
        "username": user.username,
        "display_name": user.display_name,
        "auth_type": user.auth_type,
        "is_admin": user.is_admin,
    }


@app.post("/api/auth/change-password")
async def api_change_password(req: ChangePasswordRequest, request: Request):
    """Смена пароля локального пользователя."""
    user = require_authenticated_user(request)
    if user.auth_type != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Смена пароля через веб-панель доступна только для локального пользователя",
        )
    settings = config_manager.get_settings().auth.local
    if not PasswordHasher.verify_password(req.old_password, settings.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текущий пароль указан неверно",
        )
    if len(req.new_password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Новый пароль должен содержать не менее 4 символов",
        )

    new_hash = PasswordHasher.hash_password(req.new_password)
    await config_manager.update_settings({"auth": {"local": {"password_hash": new_hash}}})
    return {"success": True, "message": "Пароль успешно обновлен"}


class TestAdRequest(BaseModel):
    server: Optional[str] = None
    port: Optional[int] = None
    use_ssl: Optional[bool] = None
    domain: Optional[str] = None
    base_dn: Optional[str] = None
    service_user: Optional[str] = None
    service_password: Optional[str] = None


@app.post("/api/auth/test-ad")
async def api_test_active_directory(
    request: Request,
    payload: Optional[TestAdRequest] = None,
):
    """Тестирование подключения к контроллеру домена."""
    require_authenticated_user(request)
    if payload:
        success, message = LdapAuthService.test_connection(
            server_host=payload.server,
            port=payload.port,
            use_ssl=payload.use_ssl,
            domain=payload.domain,
            base_dn=payload.base_dn,
            service_user=payload.service_user,
            service_password=payload.service_password,
        )
    else:
        success, message = LdapAuthService.test_connection()
    return {"success": success, "message": message}


# -------------------------------------------------------------
# REST API: Управление трансляцией и телеметрия
# -------------------------------------------------------------


@app.get("/api/status")
async def get_system_status() -> Dict[str, Any]:
    """Комплексная телеметрия системы (статус, текущий трек, очередь, ошибки, плагины, клиенты)."""
    streamer_state = streamer.get_telemetry()
    playlist_state = await playlist_mgr.get_state()
    plugins_state = plugin_mgr.get_all_schemas()
    clients_state = await client_manager.get_state()

    return {
        "streamer": streamer_state,
        "playlist": playlist_state,
        "plugins": plugins_state,
        "clients": clients_state,
    }


class PlayTrackRequest(BaseModel):
    filename: str


class MovePlaylistItemRequest(BaseModel):
    from_index: int
    to_index: int


class ReorderPlaylistRequest(BaseModel):
    items: List[str]


class DeleteFileRequest(BaseModel):
    filename: str


class RenameFileRequest(BaseModel):
    old_filename: str
    new_filename: str


class ClientAudioControlRequest(BaseModel):
    client_id: str
    audio_enabled: bool


def normalize_time_value(v: Any) -> str:
    s = str(v).strip()
    if ":" in s:
        parts = s.split(":")
        if len(parts) >= 2:
            try:
                h = int(parts[0])
                m = int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return f"{h:02d}:{m:02d}"
            except (ValueError, TypeError):
                pass
    return "08:00"


class ClientUpdateRequest(BaseModel):
    client_id: str
    custom_name: Optional[str] = None
    os_info: Optional[str] = None
    ip: Optional[str] = None
    stream_allowed: Optional[bool] = None
    audio_enabled: Optional[bool] = None
    standby: Optional[bool] = None
    schedule_mode: Optional[str] = None
    schedule_start: Optional[str] = None
    schedule_end: Optional[str] = None
    schedule_days: Optional[List[int]] = None

    @field_validator("schedule_start", "schedule_end", mode="before")
    @classmethod
    def validate_schedule_times(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return normalize_time_value(v)


class ScheduleGlobalUpdateRequest(BaseModel):
    mode: Literal["24/7", "interval"]
    start_time: str = "08:00"
    end_time: str = "20:00"
    days_of_week: List[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])
    action_off: Literal["standby", "adb_sleep"] = "standby"

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_global_times(cls, v: Any) -> str:
        return normalize_time_value(v)


class ClientScheduleUpdateRequest(BaseModel):
    client_id: str
    schedule_mode: Literal["global", "24/7", "interval"]
    schedule_start: Optional[str] = "08:00"
    schedule_end: Optional[str] = "20:00"
    schedule_days: Optional[List[int]] = None

    @field_validator("schedule_start", "schedule_end", mode="before")
    @classmethod
    def validate_client_times(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return normalize_time_value(v)


class ClientAddRequest(BaseModel):
    ip: str
    custom_name: str
    os_info: Optional[str] = "Android"
    schedule_mode: Optional[str] = "global"
    schedule_start: Optional[str] = "08:00"
    schedule_end: Optional[str] = "20:00"

    @field_validator("schedule_start", "schedule_end", mode="before")
    @classmethod
    def validate_add_times(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return normalize_time_value(v)


class ClientAdbRequest(BaseModel):
    client_id: Optional[str] = None
    ip: Optional[str] = None
    action: str = "shutdown"  # "shutdown" | "sleep" | "wakeup" | "reboot" | "get_info" | "custom"
    port: int = 5555
    custom_command: Optional[str] = None


class ClientStreamControlRequest(BaseModel):
    client_id: str
    stream_allowed: bool


class ClientStandbyRequest(BaseModel):
    client_id: str
    standby: bool


class ClientPoweroffRequest(BaseModel):
    client_id: str
    action: str = "exit_app"  # "exit_app" | "poweroff"


@app.get("/api/clients")
async def get_connected_clients():
    """Получение списка сохраненных и подключенных клиентских устройств и их статусов."""
    return await client_manager.get_state()


@app.post("/api/clients/audio-control")
async def control_client_audio(req: ClientAudioControlRequest):
    """Управление звуком для всех клиентов ('all') или конкретного устройства."""
    success = await client_manager.set_audio(req.client_id, req.audio_enabled)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Клиентское устройство с ID '{req.client_id}' не найдено"
        )
    return {
        "success": True,
        "message": f"Звук {'включен' if req.audio_enabled else 'заглушен'} для '{req.client_id}'",
        "state": await client_manager.get_state()
    }


@app.post("/api/clients/update")
async def update_client_meta(req: ClientUpdateRequest):
    """Обновление метаданных клиентского устройства (имя, ОС, IP, вещание, звук, расписание)."""
    success = await client_manager.update_client_meta(
        req.client_id,
        custom_name=req.custom_name,
        os_info=req.os_info,
        ip=req.ip,
        stream_allowed=req.stream_allowed,
        audio_enabled=req.audio_enabled,
        standby=req.standby,
        schedule_mode=req.schedule_mode,
        schedule_start=req.schedule_start,
        schedule_end=req.schedule_end,
        schedule_days=req.schedule_days,
    )
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Клиентское устройство с ID '{req.client_id}' не найдено"
        )
    # Немедленно запускаем проверку расписания при изменении параметров
    await schedule_enforcer.check_and_enforce_all()
    return {
        "success": True,
        "message": f"Данные клиента '{req.client_id}' успешно обновлены",
        "state": await client_manager.get_state()
    }


@app.post("/api/clients/stream-control")
async def control_client_stream(req: ClientStreamControlRequest):
    """Разрешение или запрет трансляции для клиента (или 'all')."""
    success = await client_manager.set_stream_allowed(req.client_id, req.stream_allowed)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Клиентское устройство с ID '{req.client_id}' не найдено"
        )
    status_str = "разрешено" if req.stream_allowed else "запрещено"
    return {
        "success": True,
        "message": f"Вещание {status_str} для '{req.client_id}'",
        "state": await client_manager.get_state()
    }


@app.post("/api/clients/standby")
async def control_client_standby(req: ClientStandbyRequest):
    """Перевод клиента в формальный спящий режим (черный экран, mute) или выход из него."""
    success = await client_manager.set_standby(req.client_id, req.standby)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Клиентское устройство с ID '{req.client_id}' не найдено"
        )
    status_str = "включен (черный экран)" if req.standby else "отключен"
    return {
        "success": True,
        "message": f"Режим Standby {status_str} для '{req.client_id}'",
        "state": await client_manager.get_state()
    }


@app.post("/api/clients/poweroff")
async def poweroff_client_endpoint(req: ClientPoweroffRequest):
    """Удаленное активное выключение (закрытие приложения или выключение ПК)."""
    success = await client_manager.poweroff_client(req.client_id, req.action)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Не удалось отправить команду выключения клиенту '{req.client_id}' (возможно, он оффлайн)"
        )
    action_str = "завершение работы приложения" if req.action == "exit_app" else "выключение ПК"
    return {
        "success": True,
        "message": f"Команда ({action_str}) отправлена клиенту '{req.client_id}'",
        "state": await client_manager.get_state()
    }


@app.delete("/api/clients/{client_id}")
async def delete_client_endpoint(client_id: str):
    """Удаление сохраненного клиента из базы."""
    success = await client_manager.delete_client(client_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Клиент '{client_id}' не найден в базе сохраненных"
        )
    return {
        "success": True,
        "message": f"Клиент '{client_id}' удален из сохраненных",
        "state": await client_manager.get_state()
    }


@app.post("/api/clients/add")
async def add_manual_client_endpoint(req: ClientAddRequest):
    """Ручное добавление устройства в реестр клиентов (например, Android TV или ТВ-приставки)."""
    if not req.ip or not req.ip.strip():
        raise HTTPException(status_code=400, detail="IP адрес обязателен для заполнения")
    client = await client_manager.add_manual_client(
        ip=req.ip.strip(),
        custom_name=req.custom_name.strip() if req.custom_name else "",
        os_info=req.os_info or "Android",
    )
    return {
        "success": True,
        "message": f"Устройство '{client.custom_name}' ({client.ip}) успешно добавлено в реестр",
        "client": client.to_dict(),
        "state": await client_manager.get_state()
    }


@app.get("/api/clients/adb-status")
async def get_adb_status_endpoint():
    """Проверка доступности утилиты ADB на сервере."""
    return {
        "available": adb_controller.is_available(),
        "path": adb_controller.get_adb_path(),
    }


@app.post("/api/clients/adb-action")
async def execute_adb_action_endpoint(req: ClientAdbRequest):
    """Выполнение сетевой команды ADB (выключение reboot -p, сон, пробуждение, перезагрузка, getprop)."""
    target_ip = req.ip
    if req.client_id:
        client_obj = client_manager.get_client(req.client_id)
        if client_obj and not target_ip:
            target_ip = client_obj.ip

    if not target_ip or not target_ip.strip():
        raise HTTPException(status_code=400, detail="IP адрес устройства не определен")

    result = await adb_controller.execute_action(
        ip=target_ip.strip(),
        port=req.port,
        action=req.action,
        custom_cmd=req.custom_command,
    )

    # Если опрос get_info прошел успешно и указан client_id — обновим его os_info в реестре
    if result.get("success") and req.action == "get_info" and req.client_id:
        formatted_os = result.get("formatted_os")
        if formatted_os:
            await client_manager.update_client_meta(req.client_id, os_info=formatted_os)

    return {
        **result,
        "state": await client_manager.get_state()
    }


# -------------------------------------------------------------
# REST API: Управление расписанием вещания (24/7 и интервалы)
# -------------------------------------------------------------


@app.get("/api/schedule")
async def get_schedule_status():
    """Получение текущего глобального расписания и статуса вещания."""
    return schedule_enforcer.get_global_status()


@app.post("/api/schedule")
async def update_global_schedule(req: ScheduleGlobalUpdateRequest):
    """Обновление глобального расписания вещания (24/7 или интервал времени)."""
    sched_dict = {
        "mode": req.mode,
        "start_time": req.start_time,
        "end_time": req.end_time,
        "days_of_week": req.days_of_week,
        "action_off": req.action_off,
    }
    await config_manager.update_settings({"schedule": sched_dict})
    await schedule_enforcer.check_and_enforce_all()
    return {
        "success": True,
        "message": f"Глобальное расписание обновлено: {'Круглосуточно 24/7' if req.mode == '24/7' else f'{req.start_time} - {req.end_time}'}",
        "schedule": schedule_enforcer.get_global_status(),
        "clients": await client_manager.get_state(),
    }


@app.post("/api/clients/schedule")
async def update_client_schedule_endpoint(req: ClientScheduleUpdateRequest):
    """Обновление индивидуального расписания вещания для клиента."""
    success = await client_manager.update_client_schedule(
        client_id=req.client_id,
        schedule_mode=req.schedule_mode,
        schedule_start=req.schedule_start,
        schedule_end=req.schedule_end,
        schedule_days=req.schedule_days,
    )
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Клиентское устройство с ID '{req.client_id}' не найдено"
        )
    await schedule_enforcer.check_and_enforce_all()
    return {
        "success": True,
        "message": f"Расписание клиента '{req.client_id}' успешно обновлено ({req.schedule_mode})",
        "state": await client_manager.get_state(),
    }


@app.get("/api/client/status")
async def get_client_public_status(
    request: Request,
    client_id: Optional[str] = None,
    token: Optional[str] = None,
    ip: Optional[str] = None,
    hostname: Optional[str] = None,
    os_info: Optional[str] = None,
    schedule_mode: Optional[str] = None,
    schedule_start: Optional[str] = None,
    schedule_end: Optional[str] = None,
    schedule_days: Optional[str] = None,
):
    """
    Публичный эндпоинт телеметрии для Android TV и внешних клиентов:
    возвращает текущее состояние вещания, standby и расписания.
    Принимает параметры расписания клиента и автоматически регистрирует/обновляет клиента.
    """
    client_ip = ip.strip() if (ip and ip.strip()) else (request.client.host if request.client else "127.0.0.1")
    search_token = token or client_id
    found_client = None
    if client_id:
        found_client = client_manager.get_client(client_id)
    if not found_client and search_token:
        found_client = next((c for c in client_manager._clients.values() if getattr(c, "token", None) == search_token), None)
    if not found_client:
        found_client = next((c for c in client_manager._clients.values() if c.ip == client_ip), None)

    days_list = None
    if schedule_days:
        try:
            days_list = [int(x.strip()) for x in schedule_days.split(",") if x.strip().isdigit()]
        except Exception:
            pass

    if not found_client and (token or client_id):
        cid = client_id or token
        client_data = {
            "token": token or cid,
            "hostname": hostname or "Android TV",
            "os_info": os_info or "Android",
            "screens": [],
            "schedule_mode": schedule_mode or "global",
            "schedule_start": schedule_start or "08:00",
            "schedule_end": schedule_end or "20:00",
            "schedule_days": days_list if days_list is not None else [1, 2, 3, 4, 5, 6, 7],
        }
        found_client = await client_manager.register_or_update(cid, client_ip, client_data, None)
    elif found_client:
        found_client.touch()
        changed = False
        if schedule_mode and schedule_mode != found_client.schedule_mode:
            found_client.schedule_mode = schedule_mode
            changed = True
        if schedule_start and schedule_start != found_client.schedule_start:
            found_client.schedule_start = schedule_start
            changed = True
        if schedule_end and schedule_end != found_client.schedule_end:
            found_client.schedule_end = schedule_end
            changed = True
        if days_list is not None and days_list != found_client.schedule_days:
            found_client.schedule_days = days_list
            changed = True
        if changed:
            client_manager._save_clients()

    global_status = schedule_enforcer.get_global_status()
    settings = config_manager.get_settings()
    global_sched = getattr(settings, "schedule", None)

    if found_client:
        in_window = found_client.is_in_schedule_window(global_schedule=global_sched)
        return {
            "client_id": found_client.client_id,
            "token": getattr(found_client, "token", found_client.client_id),
            "custom_name": found_client.custom_name,
            "ip": found_client.ip,
            "standby": found_client.standby or not in_window,
            "audio_enabled": found_client.audio_enabled,
            "stream_allowed": found_client.stream_allowed,
            "schedule_mode": found_client.schedule_mode,
            "schedule_start": found_client.schedule_start,
            "schedule_end": found_client.schedule_end,
            "is_in_schedule": in_window,
            "server_time": global_status.get("server_time"),
        }

    # Если устройство пока не зарегистрировано в базе
    return {
        "client_id": client_id or f"unregistered-{client_ip}",
        "token": search_token or client_id or f"unregistered-{client_ip}",
        "ip": client_ip,
        "standby": not global_status["is_active_now"],
        "audio_enabled": True,
        "stream_allowed": True,
        "schedule_mode": "global",
        "schedule_start": global_status.get("start_time"),
        "schedule_end": global_status.get("end_time"),
        "is_in_schedule": global_status["is_active_now"],
        "server_time": global_status.get("server_time"),
    }



@app.post("/api/playlist/rename-file")
async def rename_playlist_file_endpoint(req: RenameFileRequest):
    """Переименование файла на диске в папке media и в очереди воспроизведения."""
    try:
        new_path = await streamer.rename_media_file(req.old_filename, req.new_filename)
        scanner.trigger_scan_now()
        return {
            "success": True,
            "message": f"Файл {req.old_filename} переименован в {new_path.name}",
            "new_filename": new_path.name,
            "playlist": await playlist_mgr.get_state()
        }
    except (ValueError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при переименовании файла {req.old_filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка переименования: {str(e)}")


@app.post("/api/player/skip")
async def skip_current_track():
    """Принудительно пропустить текущий файл и перейти к следующему."""
    success = await streamer.skip_track()
    return {"success": success, "message": "Текущий трек пропущен"}


@app.post("/api/player/previous")
async def previous_track():
    """Переход к предыдущему файлу в очереди."""
    success = await streamer.previous_track()
    if not success:
        raise HTTPException(status_code=400, detail="Очередь воспроизведения пуста")
    return {"success": True, "message": "Переход к предыдущему треку"}


@app.post("/api/player/play-track")
async def play_specific_track(req: PlayTrackRequest):
    """Принудительный немедленный запуск указанного трека из очереди."""
    success = await streamer.play_track(req.filename)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Файл {req.filename} не найден в текущей очереди воспроизведения"
        )
    return {"success": True, "message": f"Запущено воспроизведение файла: {req.filename}"}


@app.post("/api/playlist/move")
async def move_playlist_item(req: MovePlaylistItemRequest):
    """Перемещение элемента очереди с позиции from_index на to_index."""
    success = await playlist_mgr.move_item(req.from_index, req.to_index)
    if not success:
        raise HTTPException(status_code=400, detail="Некорректные индексы для перемещения")
    return {
        "success": True,
        "message": "Позиция в очереди изменена",
        "playlist": await playlist_mgr.get_state()
    }


@app.post("/api/playlist/reorder")
async def reorder_playlist(req: ReorderPlaylistRequest):
    """Изменение порядка элементов очереди на основе переданного списка."""
    success = await playlist_mgr.reorder(req.items)
    if not success:
        raise HTTPException(status_code=400, detail="Некорректный список файлов для изменения порядка")
    return {
        "success": True,
        "message": "Порядок воспроизведения обновлен",
        "playlist": await playlist_mgr.get_state()
    }


@app.post("/api/playlist/delete-file")
async def delete_media_file_endpoint(req: DeleteFileRequest):
    """Удаление файла с диска из папки media и исключение из очереди."""
    try:
        deleted = await streamer.delete_media_file(req.filename)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Файл {req.filename} не найден в медиа-директории сервера"
            )
        # Синхронизируем сканер
        scanner.trigger_scan_now()
        return {
            "success": True,
            "message": f"Файл {req.filename} успешно удален с сервера",
            "playlist": await playlist_mgr.get_state()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка при удалении файла {req.filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при удалении файла: {str(e)}")


@app.post("/api/player/scan")
async def trigger_rescan():
    """Принудительно сканировать медиа-директорию прямо сейчас."""
    scanner.trigger_scan_now()
    return {"success": True, "message": "Запущено принудительное сканирование"}


@app.post("/api/player/reset-breaker")
async def reset_circuit_breaker():
    """Ручной сброс счетчика сбоев предохранителя (Circuit Breaker)."""
    await streamer.reset_circuit_breaker()
    return {"success": True, "message": "Счетчик сбоев сброшен в 0"}


# -------------------------------------------------------------
# REST API: Конфигурация и плагины
# -------------------------------------------------------------


@app.get("/api/config")
async def get_configuration():
    """Получение текущей конфигурации сервера."""
    return config_manager.get_public_settings()


@app.post("/api/config")
async def update_configuration(new_settings: Dict[str, Any]):
    """Обновление глобальных параметров конфигурации сервера."""
    try:
        updated = await config_manager.update_settings(new_settings)
        # Если обновлены настройки плагинов, обновляем кастомные модули и немедленно перезагружаем FFmpeg
        if "plugins" in new_settings:
            plugin_mgr.load_custom_plugins()
            await streamer.reload_pipeline()
        return {
            "success": True,
            "message": "Конфигурация успешно обновлена",
            "settings": config_manager.get_public_settings(),
        }
    except Exception as e:
        logger.error(f"Ошибка валидации/обновления конфигурации: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/plugins/logo/upload")
async def upload_logo_image(file: UploadFile = File(...)):
    """Загрузка файла логотипа (PNG/JPG) через веб-панель."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Имя файла не указано")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(
            status_code=400,
            detail="Поддерживаются только изображения форматов PNG, JPG, JPEG, WEBP",
        )

    config_dir = BASE_DIR / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target_path = config_dir / f"logo{ext}"

    try:
        with open(target_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        rel_path = f"config/logo{ext}"
        # Автоматически обновляем путь к логотипу в настройках и активируем плагин
        await config_manager.update_settings(
            {"plugins": {"logo": {"image_path": rel_path, "enabled": True}}}
        )

        logger.info(f"Загружен новый файл логотипа: {target_path} (плагин активирован)")
        await streamer.reload_pipeline()
        return {
            "success": True,
            "filename": target_path.name,
            "path": rel_path,
        }
    except Exception as e:
        logger.error(f"Ошибка при сохранении логотипа: {e}")
        raise HTTPException(
            status_code=500, detail=f"Ошибка сохранения файла: {str(e)}"
        )


# -------------------------------------------------------------
# REST API: Управление динамическими плагинами
# -------------------------------------------------------------


@app.get("/api/plugins/templates")
async def get_plugin_templates():
    """Возвращает заготовку Python кода и пресеты визуальных плагинов."""
    return {
        "python_starter": STARTER_PYTHON_PLUGIN_TEMPLATE,
        "visual_presets": {
            "text_ticker": {
                "name": "ticker",
                "title": "Бегущая строка",
                "type": "text_ticker",
                "config": {
                    "enabled": True,
                    "text": "Эфир телеканала • Прямая трансляция",
                    "mode": "scroll",
                    "speed": 120,
                    "position": "bottom",
                    "font_size": 24,
                    "font_color": "white",
                    "box_enabled": True,
                    "box_color": "0x00000099",
                    "margin_y": 20,
                },
            },
            "filter": {
                "name": "color_grading",
                "title": "Цветокоррекция",
                "type": "filter",
                "config": {
                    "enabled": True,
                    "filter_expr": "eq=brightness=0.03:contrast=1.12:saturation=1.2",
                    "preset": "color_boost",
                },
            },
            "image": {
                "name": "sponsor_banner",
                "title": "Спонсорский баннер",
                "type": "image",
                "config": {
                    "enabled": False,
                    "image_path": "",
                    "position": "bottom_left",
                    "scale_width": 140,
                    "opacity": 0.9,
                    "margin_x": 20,
                    "margin_y": 20,
                },
            },
        },
    }


@app.post("/api/plugins/custom/create-visual")
async def create_visual_plugin(payload: Dict[str, Any]):
    """Создание нового визуального плагина (текст/фильтр/баннер)."""
    ptype = payload.get("plugin_type")
    name = payload.get("name")
    title = payload.get("title") or name
    cfg = payload.get("config") or {}

    if not ptype or not name:
        raise HTTPException(
            status_code=400, detail="Поля 'plugin_type' и 'name' обязательны."
        )

    try:
        plugin = await plugin_mgr.register_visual_plugin(
            plugin_type=ptype,
            name=name,
            title=title,
            initial_config=cfg,
        )
        await streamer.reload_pipeline()
        return {
            "success": True,
            "message": f"Визуальный плагин '{name}' успешно создан.",
            "schema": plugin.get_settings_schema(),
        }
    except Exception as e:
        logger.error(f"Ошибка создания плагина {name}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/plugins/custom/upload-python")
async def upload_python_plugin(
    payload: Optional[Dict[str, Any]] = None,
    file: Optional[UploadFile] = File(None),
):
    """Загрузка или установка кастомного Python-плагина."""
    if file and file.filename:
        plugin_name = Path(file.filename).stem
        content_bytes = await file.read()
        content = content_bytes.decode("utf-8")
    elif payload and "code" in payload:
        plugin_name = payload.get("name")
        content = payload.get("code")
        if not plugin_name:
            raise HTTPException(status_code=400, detail="Укажите имя плагина.")
    else:
        raise HTTPException(
            status_code=400, detail="Предоставьте файл .py или строку кода."
        )

    try:
        plugin = await plugin_mgr.install_python_plugin_code(
            name=plugin_name, code=content
        )
        await streamer.reload_pipeline()
        return {
            "success": True,
            "message": f"Python-плагин '{plugin_name}' успешно установлен и активирован.",
            "schema": plugin.get_settings_schema(),
        }
    except Exception as e:
        logger.error(f"Ошибка установки Python-плагина {plugin_name}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/plugins/custom/{plugin_name}")
async def delete_custom_plugin(plugin_name: str):
    """Удаление пользовательского плагина."""
    try:
        success = await plugin_mgr.unregister_custom_plugin(plugin_name)
        if not success:
            raise HTTPException(status_code=404, detail="Плагин не найден.")
        await streamer.reload_pipeline()
        return {
            "success": True,
            "message": f"Плагин '{plugin_name}' успешно удален.",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка удаления плагина {plugin_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/plugins/custom/{plugin_name}/upload-image")
async def upload_custom_plugin_image(plugin_name: str, file: UploadFile = File(...)):
    """Загрузка изображения для кастомного графического плагина."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Имя файла не указано")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(
            status_code=400,
            detail="Поддерживаются только форматы PNG, JPG, JPEG, WEBP",
        )

    img_dir = BASE_DIR / "config" / "custom_images"
    img_dir.mkdir(parents=True, exist_ok=True)
    target_path = img_dir / f"{plugin_name}{ext}"

    try:
        with open(target_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        rel_path = f"config/custom_images/{plugin_name}{ext}"
        await config_manager.update_settings(
            {"plugins": {plugin_name: {"image_path": rel_path, "enabled": True}}}
        )
        await streamer.reload_pipeline()
        return {"success": True, "path": rel_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------
# WebSocket: Трансляция логов в реальном времени
# -------------------------------------------------------------


@app.websocket("/ws/logs")
async def websocket_logs_endpoint(websocket: WebSocket):
    """WebSocket эндпоинт для доставки логов клиентам панели управления."""
    session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        session_id = websocket.query_params.get("token")
    if not session_manager.get_session(session_id):
        await websocket.close(code=1008)
        return

    await log_broadcaster.connect(websocket)
    try:
        while True:
            # Ожидание ping/pong сообщений от клиента
            await websocket.receive_text()
    except WebSocketDisconnect:
        log_broadcaster.disconnect(websocket)
    except Exception:
        log_broadcaster.disconnect(websocket)


@app.websocket("/ws/client")
async def websocket_client_endpoint(websocket: WebSocket):
    """WebSocket канал для регистрации клиентских плееров, heartbeat и удаленных команд."""
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else "127.0.0.1"
    registered_id: Optional[str] = None
    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            if msg_type == "register":
                registered_id = str(msg.get("client_id", client_ip))
                await client_manager.register_or_update(
                    client_id=registered_id,
                    ip=client_ip,
                    data=msg,
                    websocket=websocket,
                )
                clients_state = await client_manager.get_state()
                client_obj = next(
                    (c for c in clients_state["clients"] if c["client_id"] == registered_id),
                    None
                )
                audio_on = client_obj["audio_enabled"] if client_obj else True
                stream_on = client_obj["stream_allowed"] if client_obj else True
                standby_on = client_obj["standby"] if client_obj else False
                await websocket.send_json({
                    "type": "registered",
                    "client_id": registered_id,
                    "audio_enabled": audio_on,
                    "stream_allowed": stream_on,
                    "standby": standby_on,
                })
                # Применяем расписание сразу при регистрации
                await schedule_enforcer.check_and_enforce_all()
            elif msg_type == "heartbeat":
                if registered_id:
                    await client_manager.update_heartbeat(registered_id, msg)
                    await websocket.send_json({"type": "heartbeat_ack"})
            elif msg_type == "status_update":
                if registered_id:
                    await client_manager.update_heartbeat(registered_id, msg)
    except WebSocketDisconnect:
        if registered_id:
            await client_manager.unregister(registered_id)
    except Exception as e:
        logger.debug(f"Исключение в сессии клиента {registered_id}: {e}")
        if registered_id:
            await client_manager.unregister(registered_id)
