"""
Точка входа для прямого запуска медиасервера на хосте (Windows / Linux / macOS).
Выполняет предварительную диагностику окружения:
1. Проверка версии Python (3.11+).
2. Проверка наличия бинарников ffmpeg и ffprobe в системном PATH.
3. Проверка и создание необходимых служебных директорий.
4. Запуск Uvicorn-сервера.
"""

import os
import shutil
import sys
from pathlib import Path


def check_python_version() -> None:
    """Проверка минимально допустимой версии Python."""
    if sys.version_info < (3, 11):
        print("=" * 65)
        print("❌ ОШИБКА: Требуется Python версии 3.11 или выше!")
        print(f"   Текущая версия: {sys.version}")
        print("=" * 65)
        sys.exit(1)


def check_binary(binary_name: str) -> str:
    """Проверка наличия исполняемого файла в переменной PATH."""
    path = shutil.which(binary_name)
    if not path:
        print("=" * 70)
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Утилита '{binary_name}' не найдена в системном PATH!")
        print("-" * 70)
        print("Инструкции по установке:")
        if sys.platform.startswith("win"):
            print("  [Windows]:")
            print("    1. Откройте PowerShell и выполните команду:")
            print("       winget install Gyan.FFmpeg")
            print("    2. Либо скачайте готовый архив с https://www.gyan.dev/ffmpeg/builds/")
            print("       распакуйте и добавьте папку 'bin' в переменную среды PATH.")
        elif sys.platform.startswith("linux"):
            print("  [Linux Debian/Ubuntu]:")
            print("    sudo apt update && sudo apt install -y ffmpeg")
            print("  [Linux Arch]:")
            print("    sudo pacman -S ffmpeg")
            print("  [Linux Fedora/CentOS]:")
            print("    sudo dnf install ffmpeg")
        elif sys.platform == "darwin":
            print("  [macOS]:")
            print("    brew install ffmpeg")
        print("=" * 70)
        sys.exit(1)
    return path


def ensure_directories() -> None:
    """Создание необходимых директорий при первом запуске."""
    for folder in ["media", "config", "logs", "static/js", "templates"]:
        p = Path(folder)
        p.mkdir(parents=True, exist_ok=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    check_python_version()

    print("\n🚀 Инициализация медиасервера непрерывного вещания...")
    ffmpeg_path = check_binary("ffmpeg")
    ffprobe_path = check_binary("ffprobe")

    print(f"  ✓ FFmpeg обнаружен:  {ffmpeg_path}")
    print(f"  ✓ FFprobe обнаружен: {ffprobe_path}")

    ensure_directories()

    try:
        import uvicorn
        from core.config import config_manager
    except ImportError as e:
        print("=" * 70)
        print(f"❌ ОШИБКА: Не установлены необходимые Python зависимости: {e}")
        print("   Выполните установку: pip install -r requirements.txt")
        print("=" * 70)
        sys.exit(1)

    settings = config_manager.get_settings()
    host = settings.web_host
    port = settings.web_port

    import atexit
    import socket
    import subprocess

    def is_port_in_use(check_port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", check_port)) == 0

    mediamtx_proc = None
    server_dir = Path(__file__).resolve().parent

    # Поиск бинарника MediaMTX
    candidate_mediamtx = [
        server_dir / "mediamtx.exe",
        server_dir / "mediamtx",
        shutil.which("mediamtx"),
    ]
    mediamtx_bin = next((str(p) for p in candidate_mediamtx if p and os.path.exists(str(p))), None)

    if not is_port_in_use(8554):
        if mediamtx_bin:
            try:
                mediamtx_proc = subprocess.Popen(
                    [mediamtx_bin],
                    cwd=str(server_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                atexit.register(lambda: mediamtx_proc.terminate() if mediamtx_proc else None)
                print(f"  ✓ MediaMTX потоковый сервер запущен: {mediamtx_bin} (RTSP :8554, HLS :8888)")
            except Exception as ex:
                print(f"  ⚠ Не удалось автоматически запустить MediaMTX: {ex}")
        else:
            print("  ℹ Внимание: порт 8554 не активен и бинарник mediamtx не найден.")
            print("    Для трансляции запустите MediaMTX отдельно или через Docker Compose.")
    else:
        print("  ✓ Порт 8554 активен (RTSP сервер доступен).")

    print(f"  ✓ Веб-панель управления доступна по адресу: http://localhost:{port}")
    print(f"  ✓ Целевой RTSP стрим: {settings.rtsp_target_url}")
    print("=" * 70)

    try:
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            reload=False,
            log_level="warning",
        )
    finally:
        if mediamtx_proc:
            try:
                mediamtx_proc.terminate()
                mediamtx_proc.wait(timeout=2)
            except Exception:
                pass


if __name__ == "__main__":
    main()
