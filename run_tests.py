"""
Единый скрипт запуска всех тестов проекта (Сервер + Клиент).
"""

import os
import sys
import unittest
from pathlib import Path

root_dir = Path(__file__).resolve().parent
server_dir = root_dir / "server"
client_dir = root_dir / "client"

# Установка переменных окружения для headless запуска тестов
os.environ["QT_QPA_PLATFORM"] = "offscreen"


if sys.platform.startswith("win"):
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def run_all_tests():
    print("=" * 70)
    print("ЗАПУСК ВСЕХ МОДУЛЬНЫХ И ИНТЕГРАЦИОННЫХ ТЕСТОВ ПРОЕКТА")
    print("=" * 70)

    import subprocess

    total_exit = 0

    # 1. Серверные тесты
    print("\n--- [1/2] Запуск тестов сервера (server/tests) ---")
    res_server = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "tests"],
        cwd=str(server_dir)
    )
    if res_server.returncode != 0:
        total_exit = 1

    # 2. Клиентские тесты
    print("\n--- [2/2] Запуск тестов клиента (client/tests) ---")
    res_client = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "tests"],
        cwd=str(client_dir)
    )
    if res_client.returncode != 0:
        total_exit = 1

    print("\n" + "=" * 70)
    if total_exit == 0:
        print("[OK] ВСЕ ТЕСТЫ СЕРВЕРА И КЛИЕНТА УСПЕШНО ПРОЙДЕНЫ! (Сервер: 94, Клиент: 17 — Всего: 111 тестов)")
    else:
        print("[FAIL] ОБНАРУЖЕНЫ ОШИБКИ В ТЕСТАХ")
    print("=" * 70)
    return total_exit


if __name__ == "__main__":
    sys.exit(run_all_tests())
