"""
Модуль логирования с поддержкой ротации файлов (logs/server.log)
и рассылки логов в реальном времени подключенным WebSocket-клиентам.
"""

import asyncio
import json
import logging
import sys
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Set

from fastapi import WebSocket


class WebSocketLogHandler(logging.Handler):
    """
    Кастомный хэндлер логирования, пересылающий форматированные записи
    в очередь asyncio для последующего броадкаста через WebSocket.
    """

    def __init__(self, broadcast_queue: asyncio.Queue, max_history: int = 150):
        super().__init__()
        self.broadcast_queue = broadcast_queue
        self.history: deque = deque(maxlen=max_history)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            log_item = {
                "timestamp": datetime.fromtimestamp(record.created).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
                "formatted": msg,
            }
            self.history.append(log_item)
            # Неблокирующая отправка в очередь
            try:
                self.broadcast_queue.put_nowait(log_item)
            except asyncio.QueueFull:
                pass
        except Exception:
            self.handleError(record)


class LogBroadcaster:
    """
    Управление активными WebSocket соединениями и рассылка новых логов.
    """

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.handler: WebSocketLogHandler = WebSocketLogHandler(self.queue)
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        """Запуск фоновой задачи броадкаста логов."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._broadcast_worker())

    async def stop(self) -> None:
        """Остановка фоновой задачи."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def connect(self, websocket: WebSocket) -> None:
        """Подключение нового клиента и отправка последних логов из истории."""
        await websocket.accept()
        self.active_connections.add(websocket)
        # Отправляем накопленную историю
        for item in list(self.handler.history):
            try:
                await websocket.send_text(json.dumps(item, ensure_ascii=False))
            except Exception:
                break

    def disconnect(self, websocket: WebSocket) -> None:
        """Отключение клиента."""
        self.active_connections.discard(websocket)

    async def _broadcast_worker(self) -> None:
        """Цикл чтения из очереди и рассылки подключенным вебсокетам."""
        while True:
            try:
                log_item = await self.queue.get()
                if not self.active_connections:
                    self.queue.task_done()
                    continue

                payload = json.dumps(log_item, ensure_ascii=False)
                dead_connections = set()

                for ws in self.active_connections:
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        dead_connections.add(ws)

                for dead in dead_connections:
                    self.active_connections.discard(dead)

                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Защита от падения воркера
                print(f"[LogBroadcaster Error] {e}")


BASE_DIR = Path(__file__).resolve().parent.parent

# Экземпляр броадкастера
log_broadcaster = LogBroadcaster()


def setup_logging(logs_dir: Path | str | None = None) -> logging.Logger:
    """
    Инициализация системы логирования с ротацией в файл logs/server.log
    и подключением консоли и WebSocket-хэндлера.
    """
    if logs_dir is None:
        logs_dir = BASE_DIR / "logs"
    else:
        logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "server.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Очистка ранее установленных хэндлеров во избежание дублирования
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Файловый хэндлер с ротацией (до 5 МБ, 5 файлов резерва)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 2. Консольный вывод с поддержкой UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 3. WebSocket хэндлер
    ws_handler = log_broadcaster.handler
    ws_handler.setLevel(logging.INFO)
    ws_handler.setFormatter(formatter)
    root_logger.addHandler(ws_handler)

    # Заглушаем излишне шумные сторонние логи
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return root_logger
