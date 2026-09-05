# 🖥️ Continuous Broadcast Media Server (`server/`)

Серверная часть системы непрерывного вещания видео. Обеспечивает автоматическое сканирование медиафайлов, управление очередью воспроизведения (плейлистом), перекодирование видеопотока через **FFmpeg**, раздачу потоков **RTSP** и **HLS** через медиасервер **MediaMTX**, веб-панель оператора на **FastAPI** и WebSocket-контроллер подключенных клиентских мониторов.

---

## 📋 Оглавление
1. [Архитектура и компоненты](#-архитектура-и-компоненты)
2. [Системные требования](#-системные-требования)
3. [Установка зависимостей](#-установка-зависимостей)
4. [Компиляция и сборка (Docker / PyInstaller)](#-компиляция-и-сборка)
5. [Запуск сервера](#-запуск-сервера)
6. [Конфигурация и переменные окружения](#-конфигурация-и-переменные-окружения)
7. [REST API и WebSocket](#-rest-api-и-websocket)
8. [Запуск тестов](#-запуск-тестов)

---

## 🏗️ Архитектура и компоненты

- **`run.py`**: Точка входа для запуска на хосте. Выполняет автоматическую проверку окружения (Python 3.11+, доступность FFmpeg/FFprobe в PATH, наличие MediaMTX), создает каталоги и запускает параллельно MediaMTX и Uvicorn.
- **`main.py`**: Главный модуль FastAPI. Содержит маршруты REST API, аутентификацию (Local/LDAP), раздачу статики и шаблонов веб-панели, обработку WebSocket соединений (`/ws/logs` и `/ws/client`).
- **`core/streamer.py` (`StreamOrchestrator`)**: Управляет процессом FFmpeg, отправляющим поток в `rtsp://localhost:8554/live`. Оснащен механизмом **Circuit Breaker** для защиты от сбоев при повреждении видеофайлов, методами безопасного удаления и переименования с освобождением дескрипторов Windows.
- **`core/playlist.py` (`PlaylistManager`)**: Потокобезопасная очередь треков (`asyncio.Lock`) с поддержкой приоритетной вставки, циклического перемещения, переупорядочивания и переименования.
- **`core/scanner.py` (`MediaScanner`)**: Фоновый сервис периодического мониторинга директории `media/` для автоматического добавления новых файлов в плейлист.
- **`core/client_manager.py` (`ClientManager`)**: Учет подключенных десктопных клиентов по WebSocket. Управляет телеметрией (список экранов, статус онлайна) и удаленным включением/отключением звука.
- **`core/plugins/`**: Динамические визуальные наложения FFmpeg: бегущая строка, логотипы, фильтры цветокоррекции.
- **`core/crypto.py`**: Модуль криптографической защиты (симметричное шифрование AES-128/Fernet) учетных данных Active Directory.
- **`mediamtx.exe` + `mediamtx.yml`**: Встроенный RTSP/HLS медиасервер.

---

## ⚙️ Системные требования

- **ОС**: Windows 10/11 / Windows Server, Linux (Ubuntu 20.04+, Debian 11+, RHEL/CentOS), macOS 12+.
- **Python**: версия **3.11** или новее.
- **FFmpeg и FFprobe**: должны быть установлены и доступны в системной переменной `PATH`.

### Установка FFmpeg:
- **Windows**:
  ```powershell
  winget install Gyan.FFmpeg
  ```
  *Либо скачайте сборку с [gyan.dev](https://www.gyan.dev/ffmpeg/builds/), распакуйте и добавьте папку `bin` в PATH.*
- **Linux (Debian/Ubuntu)**:
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```
- **Linux (Arch)**:
  ```bash
  sudo pacman -S ffmpeg
  ```
- **macOS**:
  ```bash
  brew install ffmpeg
  ```

Проверьте корректность установки утилит:
```bash
ffmpeg -version
ffprobe -version
```

---

## 📦 Установка зависимостей

### 1. Создание виртуального окружения
Перейдите в директорию `server/`:
```bash
cd server
python -m venv .venv
```

### 2. Активация окружения
- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **Windows (CMD)**:
  ```cmd
  .\.venv\Scripts\activate.bat
  ```
- **Linux / macOS**:
  ```bash
  source .venv/bin/activate
  ```

### 3. Установка Python-библиотек
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Подготовка служебных папок
Сервер автоматически создает директории при старте, либо их можно создать вручную:
```bash
mkdir media config logs
```
Поместите ваши видеофайлы (`.mp4`, `.mkv`, `.avi`, `.mov`) в директорию `media/`.

---

## 🔨 Компиляция и сборка

### Вариант 1: Сборка и запуск в Docker (Рекомендуется для Linux-серверов)

В директории `server/` подготовлены готовые `Dockerfile` и `docker-compose.yml`.

#### Сборка отдельного Docker-образа:
```bash
docker build -t stream-core-server .
```

#### Запуск полного стека через Docker Compose:
Docker Compose автоматически разворачивает сервис **MediaMTX** (RTSP: 8554, HLS: 8888, RTMP: 1935) и сервис **stream-core** (веб-панель: 8000, FFmpeg):
```bash
docker-compose up -d --build
```

#### Просмотр логов контейнера:
```bash
docker-compose logs -f stream-core
```

#### Остановка сервисов:
```bash
docker-compose down
```

---

### Вариант 2: Сборка автономного бинарного файла через PyInstaller (Windows/Linux)

Для запуска сервера без установленного Python можно скомпилировать исполняемый файл:

1. Установите PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Выполните сборку:
   ```powershell
   pyinstaller --noconfirm --onedir --console `
       --name "stream-server" `
       --add-data "templates;templates" `
       --add-data "static;static" `
       --add-data "mediamtx.exe;." `
       --add-data "mediamtx.yml;." `
       --add-data "auto.crt;." `
       --add-data "auto.key;." `
       run.py
   ```
Исполняемый пакет будет сформирован в папке `dist/stream-server/`.

---

## 🚀 Запуск сервера

### Способ 1: Прямой запуск оркестратора (Основной способ)
```powershell
cd server
python run.py
```
Скрипт `run.py`:
1. Проверяет версию Python и наличие `ffmpeg`/`ffprobe`.
2. Запускает фоновый процесс `mediamtx.exe` (на портах 8554 и 8555).
3. Запускает сервер Uvicorn с приложением FastAPI на порту 8000.
4. Отслеживает корректное завершение всех процессов при нажатии `Ctrl + C`.

Веб-интерфейс панели управления доступен по адресу:
👉 **`http://localhost:8000`**

### Способ 2: Запуск только веб-приложения через Uvicorn
Если MediaMTX запущен отдельно (например, как служба или контейнер):
```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## ⚙️ Конфигурация и переменные окружения

Сервер поддерживает конфигурацию через переменные среды (`.env` или системные):

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `WEB_PORT` | `8000` | Порт веб-панели управления и REST API |
| `RTSP_TARGET_URL` | `rtsp://localhost:8554/live` | Целевой RTSP-адрес публикации FFmpeg |
| `MEDIAMTX_HLS_URL` | `http://localhost:8888/live` | HLS-адрес потока MediaMTX |
| `MEDIA_DIR` | `./media` | Путь к каталогу с видеофайлами |
| `CONFIG_PATH` | `./config/settings.json` | Путь к файлу персистентных настроек |
| `LOG_LEVEL` | `INFO` | Уровень логирования (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SECRET_KEY` | `super-secret-key-change-it` | Ключ подписи сессионных кук веб-панели |

Постоянные настройки плейлиста, переходов, плагинов и сканирования сохраняются в формате JSON в `config/settings.json`.

---

## 🔌 REST API и WebSocket

### Основные эндпоинты REST API:
- `GET /api/status` — Полное состояние сервера: статус плеера, текущий трек, длина очереди, подключенные клиенты, метрики CPU/RAM.
- `GET /api/playlist` — Список файлов в очереди воспроизведения с метаданными.
- `POST /api/player/play-track` — `{"filename": "video.mp4"}` — принудительный запуск конкретного видео.
- `POST /api/player/skip` — Переход к следующему треку очереди.
- `POST /api/player/previous` — Возврат к предыдущему воспроизведенному треку.
- `POST /api/playlist/move` — `{"filename": "video.mp4", "new_index": 0}` — перемещение трека в очереди.
- `POST /api/playlist/rename-file` — `{"old_filename": "old.mp4", "new_filename": "new.mp4"}` — безопасное переименование с освобождением файла Windows.
- `POST /api/playlist/delete-file` — `{"filename": "clip.mp4"}` — безопасное удаление с диска.
- `GET /api/clients` — Список всех сохраненных и активных десктопных клиентов с мониторами, понятным именем и состоянием.
- `POST /api/clients/audio-control` — `{"client_id": "all"|"uuid", "audio_enabled": true|false}` — удаленное глушение звука.
- `POST /api/clients/update` — `{"client_id": "uuid", "custom_name": "Кабинет 101"}` — переименование клиента.
- `POST /api/clients/stream-control` — `{"client_id": "all"|"uuid", "stream_allowed": true|false}` — разрешение/запрет показа видеотрансляции.
- `POST /api/clients/standby` — `{"client_id": "all"|"uuid", "standby": true|false}` — формальное выключение (черный экран без звука).
- `POST /api/clients/poweroff` — `{"client_id": "uuid", "action": "exit_app"|"poweroff"}` — активное выключение (закрытие приложения или выключение ПК).
- `DELETE /api/clients/{client_id}` — удаление клиента из сохраненной базы `config/clients.json`.

### WebSocket эндпоинты:
- `ws://<HOST>:8000/ws/logs` — Трансляция серверных системных логов в веб-консоль реального времени.
- `ws://<HOST>:8000/ws/client` — Канал двусторонней связи с десктопными клиентами (регистрация, heartbeat, удаленный mute, синхронизация настроек и команд выключения).

---

## 🧪 Запуск тестов

В модуле сервера реализовано **72 автоматических теста** (модульные тесты плейлиста, оркестратора, сканера, криптографии, аутентификации, клиентов и API).

Запуск тестов сервера:
```powershell
cd server
python -m unittest discover tests
```

Ожидаемый результат:
```text
Ran 72 tests in 1.5s
OK
```
