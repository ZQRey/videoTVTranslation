"""
Точка входа десктопного мультиэкранного плеера вещания.
Обрабатывает аргументы командной строки, инициализирует libVLC, настраивает окружение Qt
и запускает AppController.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Принудительная установка UTF-8 для консоли Windows
if sys.platform.startswith("win"):
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # Регистрация путей поиска libVLC DLL
    vlc_search_dirs = [
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
    ]
    for vlc_dir in vlc_search_dirs:
        if os.path.exists(vlc_dir):
            try:
                os.add_dll_directory(vlc_dir)
            except AttributeError:
                pass
            os.environ["PATH"] = vlc_dir + os.pathsep + os.environ.get("PATH", "")
            break

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app_controller import AppController
from config import ConfigManager


def setup_logging(debug: bool = False) -> None:
    """Настройка форматированного логирования."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def parse_args() -> argparse.Namespace:
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Кроссплатформенный мультиэкранный RTSP плеер непрерывного вещания"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="IP-адрес или домен сервера вещания (переопределяет client_config.json)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="RTSP порт сервера (по умолчанию: 8554)"
    )
    parser.add_argument(
        "--stream",
        type=str,
        default=None,
        help="Имя потока (по умолчанию: live)"
    )
    parser.add_argument(
        "--caching",
        type=int,
        default=None,
        help="Сетевой буфер VLC в миллисекундах (по умолчанию: 300)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Путь к пользовательскому файлу client_config.json"
    )
    parser.add_argument(
        "--reset-config",
        action="store_true",
        help="Сбросить конфигурацию на значения по умолчанию"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Включить подробный отладочный вывод (DEBUG уровень)"
    )
    return parser.parse_args()


def main() -> None:
    """Главная функция инициализации и запуска плеера."""
    args = parse_args()
    setup_logging(args.debug)
    logger = logging.getLogger("desktop_player.main")

    logger.info("=" * 60)
    logger.info("Запуск Continuous Broadcast Desktop Player (PyQt6 + libVLC)")
    logger.info("Платформа: %s | Python: %s", sys.platform, sys.version.split()[0])
    logger.info("=" * 60)

    # Инициализация менеджера конфигурации
    config_mgr = ConfigManager(config_path=args.config)

    if args.reset_config:
        logger.info("Запрошен сброс конфигурации. Применение настроек по умолчанию.")
        config_mgr.update(server_host="", rtsp_port=8554, stream_path="live", network_caching=300)

    # Применение CLI переопределений, если они заданы
    current_cfg = config_mgr.config
    host = args.host if args.host is not None else current_cfg.server_host
    port = args.port if args.port is not None else current_cfg.rtsp_port
    path = args.stream if args.stream is not None else current_cfg.stream_path
    caching = args.caching if args.caching is not None else current_cfg.network_caching

    if (
        host != current_cfg.server_host
        or port != current_cfg.rtsp_port
        or path != current_cfg.stream_path
        or caching != current_cfg.network_caching
    ):
        logger.info("Параметры командной строки переопределяют файл конфигурации.")
        config_mgr.update(
            server_host=host,
            rtsp_port=port,
            stream_path=path,
            network_caching=caching
        )

    # Создание экземпляра Qt приложения
    app = QApplication(sys.argv)
    app.setApplicationName("Continuous Broadcast Player")
    app.setOrganizationName("MediaStreamEngine")

    # Инициализация контроллера
    controller = AppController(config_manager=config_mgr)
    controller.start()

    # Запуск главного цикла событий Qt
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
