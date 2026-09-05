# 📺 Continuous Broadcast Video System: Документация и руководство для разработчика (Agent Guide)

> **Назначение данного файла**: Этот документ содержит исчерпывающее описание архитектуры, компонентов, соглашений, неочевидных инженерных нюансов ("подводных камней") и инструкций по запуску/тестированию для разработчиков и AI-агентов, работающих с проектом.

---

## 1. Обзор проекта и назначение

Проект представляет собой распределённую клиент-серверную систему для непрерывной круглосуточной трансляции видеоконтента по протоколу RTSP и его одновременного синхронного воспроизведения на физических мониторах и телевизорах:
1. **Сервер (`server/`)**:
   - Автоматически сканирует директорию с медиафайлами (`server/media/`).
   - Ведёт непрерывное зацикленное вещание через связку **FFmpeg** + **MediaMTX** (RTSP сервер).
   - Предоставляет веб-панель управления (FastAPI) с возможностью онлайн-мониторинга, изменения очерёдности, принудительного выбора трека, переименования, удаления файлов, наложения динамических визуальных плагинов (титры, логотипы, бегущая строка) и удалённого контроля подключенных клиентов.
2. **Клиент (`client/`)**:
   - Автономное кроссплатформенное десктопное приложение (PyQt6 + libVLC).
   - Автоматически находит **все подключенные физические экраны и телевизоры**.
   - Создает на каждом экране полноэкранное безрамочное окно плеера с аппаратным декодированием видео.
   - Воспроизводит видео синхронно на всех экранах, но **звук подает строго на один монитор (Primary)** во избежание эха.
   - Поддерживает динамический хотплаг дисплеев (подключение/отключение на лету).
   - Держит WebSocket-соединение с сервером: передаёт телеметрию (экраны, ОС, IP, пинги) и принимает команды удалённого управления звуком.
   - Поддерживает горячие клавиши: `Q` (настройки) и `E` (чистый выход).

---

## 2. Структура директорий проекта

```text
videoTVTranslation/
├── .venv/                      # Общее виртуальное окружение Python (3.10+ / 3.14)
├── PROJECT_OVERVIEW.md         # Данный документ (исчерпывающее руководство)
├── AGENTS.md                   # Краткие правила и инструкции для AI-агентов
├── run_tests.py                # Единый раннер всех тестов проекта (97 тестов)
│
├── server/                     # СЕРВЕРНАЯ ЧАСТЬ (Python / FastAPI / MediaMTX)
│   ├── run.py                  # Точка входа: одновременный запуск MediaMTX + FastAPI
│   ├── main.py                 # FastAPI приложение (REST API, WebSocket, Web Dashboard, Auth)
│   ├── mediamtx.exe            # Исполняемый файл RTSP сервера (MediaMTX)
│   ├── mediamtx.yml            # Конфигурация MediaMTX (RTSP порт 8554, API 8555)
│   ├── auto.crt, auto.key      # SSL-сертификаты MediaMTX
│   ├── requirements.txt        # Зависимости сервера (fastapi, uvicorn, pydantic, ldap3 и др.)
│   ├── bin/                    # Комплектные бинарники (bin/adb/windows/adb.exe и библиотеки)
│   ├── core/                   # Ядро сервера (streamer, playlist, scanner, client_manager, adb_controller, auth)
│   ├── media/                  # Директория с медиафайлами для трансляции (.mp4, .mkv, .avi)
│   ├── config/                 # Хранилище настроек (settings.json, clients.json)
│   ├── templates/              # HTML-шаблоны Jinja2 (index.html, login.html)
│   ├── static/                 # Статика фронтенда (js/app.js, css)
│   └── tests/                  # Серверные тесты (81 тест)
│
├── client/                     # ДЕСКТОПНЫЙ КЛИЕНТ (Python / PyQt6 / VLC)
│   ├── main.py                 # Точка входа клиента: запуск QApplication и AppController
│   ├── app_controller.py       # AppController: обнаружение экранов, хотплаг, WS, детальная ОС, GlobalKeyFilter
│   ├── player_window.py        # PlayerWindow: нативное окно PyQt6 с внедренным libVLC winId
│   ├── settings_dialog.py      # SettingsDialog: модальное окно настроек адреса RTSP потока
│   ├── config.py               # ClientConfig, ConfigManager: строго stdlib (без pydantic)
│   ├── client_config.json      # Локальный конфиг клиента (host, port, path, client_id)
│   ├── vlc-help.txt            # Справочник аргументов libVLC
│   ├── requirements.txt        # Зависимости клиента (PyQt6, python-vlc)
│   └── tests/                  # Клиентские тесты (16 тестов)
│
└── android/                    # ANDROID TV & MOBILE КЛИЕНТ (Kotlin / Media3 ExoPlayer)
    ├── app/                    # Модуль приложения
    │   ├── src/main/           # Код Kotlin, разметка XML, Android TV Leanback манифест
    │   └── build.gradle.kts    # Зависимости Media3 (RTSP TCP, HLS), Leanback, DataStore
    ├── build.gradle.kts        # Корневой Gradle-скрипт
    ├── settings.gradle.kts     # Конфигурация репозиториев и модулей
    └── README.md               # Руководство по сборке APK и установке на Android TV
```

---

## 3. Архитектура и ключевые модули

### 3.1. Сервер (`server/`)

| Модуль | Класс / Сущность | Назначение |
|---|---|---|
| `core/streamer.py` | `StreamOrchestrator` | Управляет подпроцессом FFmpeg, считывает видео из `media/` и вещает в `rtsp://localhost:8554/live`. Содержит Circuit Breaker (защита от битых файлов), методы `skip_track()`, `previous_track()`, `play_track(filename)`, безопасное `delete_media_file()` и `rename_media_file()`. |
| `core/playlist.py` | `PlaylistManager` | Потокобезопасная очередь треков (`asyncio.Lock`). Поддерживает вставку новых файлов сразу за текущим, ручной выбор трека, циклический переход назад, перемещение элементов (`move_item`), пакетное изменение очерёдности (`reorder`), переименование (`rename_file`). |
| `core/scanner.py` | `MediaScanner` | Фоновый цикл периодического сканирования папки `media/` (интервал настраивается). Синхронизирует изменения с `PlaylistManager`. |
| `core/client_manager.py` | `ClientManager`, `ClientDevice` | Учёт и персистентное хранение клиентов (`config/clients.json`). Принимает WebSocket-сессии `/ws/client`, сохраняет понятные имена (`custom_name`), статус онлайна, список экранов, IP. Управляет звуком (`set_audio`), вещанием (`set_stream_allowed`), режимом ожидания (`set_standby`), активным выключением (`poweroff_client`) и удалением (`delete_client`). |
| `core/plugins/` | `PluginManager` | Позволяет накладывать эффекты FFmpeg на лету: фильтры цветокоррекции, бегущая строка (`drawtext`), водяные знаки/баннеры (`movie/overlay`). |
| `core/crypto.py` | `encrypt_secret`, `decrypt_secret` | Симметричное шифрование паролей и секретов Active Directory (AES-128/Fernet) с сохранением мастер-ключа в `.secret.key`. |
| `main.py` | FastAPI App | Маршруты REST API: управление плеером (`/api/player/*`), плейлистом (`/api/playlist/*`), клиентами (`/api/clients/*`), плагинами (`/api/plugins/*`). WebSocket `/ws/logs` и `/ws/client`. |

#### Спецификация REST API сервера:
- `GET /api/status` — текущее состояние плеера, плейлиста, клиентов и системных метрик.
- `GET /api/clients` — список всех сохраненных клиентов со статусами онлайна, мониторами, звуком, вещанием и standby.
- `POST /api/clients/audio-control` — включение/отключение звука (`{"client_id": "all"|"id", "audio_enabled": bool}`).
- `POST /api/clients/update` — переименование клиента (`{"client_id": str, "custom_name": str}`).
- `POST /api/clients/stream-control` — разрешение/запрет стрима (`{"client_id": "all"|"id", "stream_allowed": bool}`).
- `POST /api/clients/standby` — режим ожидания / черный экран (`{"client_id": "all"|"id", "standby": bool}`).
- `POST /api/clients/poweroff` — активное выключение (`{"client_id": str, "action": "exit_app"|"poweroff"}`).
- `POST /api/clients/add` — ручное добавление устройства по IP (например, Android TV).
- `GET /api/clients/adb-status` — проверка статуса и пути к утилите ADB на сервере.
- `POST /api/clients/adb-action` — выполнение команды ADB по сети (shutdown, sleep, wakeup, reboot, get_info).
- `DELETE /api/clients/{client_id}` — удаление клиента из сохраненных.
- `POST /api/playlist/rename-file` — `{"old_filename": str, "new_filename": str}`.
- `POST /api/playlist/delete-file` — `{"filename": str}`.
- `POST /api/player/play-track` — `{"filename": str}`.
- `POST /api/player/skip` / `POST /api/player/previous` — переключение треков.
- `POST /api/playlist/move` / `POST /api/playlist/reorder` — изменение порядка.

---

### 3.2. Клиент (`client/`)

| Модуль | Класс / Сущность | Назначение |
|---|---|---|
| `main.py` | Скрипт запуска | Проверяет версию Python (3.10+), нативную библиотеку VLC, создает `QApplication` и `AppController`. |
| `app_controller.py` | `AppController` | Центральный контроллер. Обнаруживает экраны через `QGuiApplication.screens()`, слушает сигналы `screenAdded`, `screenRemoved`, `primaryScreenChanged`. Подключается к серверу по WebSocket (`QWebSocket`). |
| `app_controller.py` | `GlobalKeyFilter` | Фильтр событий клавиатуры на уровне `QApplication`. Ловит `Q` / `й` (открытие настроек) и `E` / `у` (выход из приложения). |
| `player_window.py` | `PlayerWindow` | Безрамочное окно (`Qt.WindowType.FramelessWindowHint`). Встраивает `vlc.MediaPlayer` через `set_hwnd(int(winId))` (Windows) или `set_xwindow` (Linux). Управляет mute звука в зависимости от `is_primary` и `audio_allowed`. |
| `settings_dialog.py` | `SettingsDialog` | Диалог настройки подключения к серверу (RTSP хост, порт, путь стрима). |
| `config.py` | `ClientConfig`, `ConfigManager` | Конфигурация клиента. Написана **строго на стандартной библиотеке Python (dataclasses)** без внешних зависимостей. |

---

## 4. Критические инженерные правила и "Подводные камни" (Gotchas)

### ⚠️ 1. Дедлок libVLC на живом RTSP-потоке (НЕ вызывать `pause()`)
- **Проблема**: Вызов нативного метода `_vlc_player.pause()` на живом RTSP-потоке Live555 переводит сетевой сокет в аварийное состояние. Последующий вызов `play()` или `stop()` намертво блокирует внутренний мьютекс VLC, вешая весь поток GUI Qt.
- **Решение**: Метод `pause()` в [player_window.py](file:///c:/Users/zqrey3/Desktop/Scritps/Python/videoTVTranslation/client/player_window.py) **не вызывает `_vlc_player.pause()`**. Вместо этого он временно заглушает звук (`audio_set_mute(True)`). Видео продолжает декодироваться в фоне без подвисания сокета. Метод `resume()` восстанавливает маршрутизацию звука.

### ⚠️ 2. Блокировка дескрипторов файлов в Windows (`WinError 32`)
- **Проблема**: На Windows нельзя удалить (`unlink()`) или переименовать (`rename()`) файл, который в данный момент открыт процессом FFmpeg.
- **Решение**: В методах `delete_media_file()` и `rename_media_file()` в [streamer.py](file:///c:/Users/zqrey3/Desktop/Scritps/Python/videoTVTranslation/server/core/streamer.py):
  1. Проверяется, играет ли этот файл прямо сейчас (`self.current_file == target_path`).
  2. Если да — процесс FFmpeg принудительно и штатно завершается (`_terminate_current_process()`).
  3. Делается задержка 200–500 мс для закрытия дескрипторов Windows.
  4. Выполняется `unlink()` или `rename()`.
  5. При переименовании текущего трека сразу возобновляется воспроизведение нового файла с установкой `_manual_switch_requested = True`.

### ⚠️ 3. Двуязычные горячие клавиши (Русская и Английская раскладки)
- **Проблема**: При русской раскладке клавиша `Q` генерирует символ `й`, а клавиша `E` генерирует `у`. При этом событие `QKeyEvent.key()` на некоторых системах или модификациях может не сопоставляться с `Qt.Key.Key_Q` / `Key_E`.
- **Решение**: В `GlobalKeyFilter` проверяется:
  - Для настроек: `key == Qt.Key.Key_Q or text in ("q", "й")`.
  - Для выхода: `key == Qt.Key.Key_E or text in ("e", "у")`.
  - Запуск колбэка обязательно оформляется через `QTimer.singleShot(0, cb)`, чтобы не блокировать обработку события внутри `eventFilter`.

### ⚠️ 4. Маршрутизация звука (Только Primary + Разрешение сервера)
- **Правило**: Звук подается на аудиокарту **только с одного физического экрана (основного)**. Все остальные экраны принудительно заглушаются (`audio_set_mute(True)`), иначе будет слышно эхо из-за микрозадержек декодирования.
- **Удалённый контроль**: Даже на основном экране звук будет активен только тогда, когда `self.audio_allowed == True` (состояние, транслируемое сервером через WebSocket).

### ⚠️ 5. Защита от Path Traversal
- Любые манипуляции с файлами (удаление, переименование, выбор) обязательно валидируются:
  - `safe_name = Path(filename).name`
  - `if safe_name != filename: raise ValueError(...)`
  - Проверка `target_path.is_relative_to(media_dir)`.

### ⚠️ 6. Автономность клиента (Zero external dependencies кроме PyQt6 и vlc)
- Клиентский модуль `config.py` написан без сторонних библиотек вроде `pydantic`. Не добавлять в клиент зависимости, не входящие в стандартную библиотеку Python, кроме `PyQt6` и `python-vlc`.

---

## 5. Инструкции по запуску и тестированию

### 5.1. Запуск автоматических тестов (Всегда выполнять после любых правок!)
В корне репозитория запустить:
```powershell
python run_tests.py
```
**Ожидаемый результат**:
```text
[OK] ВСЕ ТЕСТЫ СЕРВЕРА И КЛИЕНТА УСПЕШНО ПРОЙДЕНЫ! (Сервер: 61, Клиент: 15 — Всего: 76 тестов)
```

### 5.2. Запуск сервера вещания
```powershell
cd server
python run.py
```
- MediaMTX запустится на портах `8554` (RTSP) и `8555` (API).
- Веб-панель управления будет доступна в браузере по адресу: `http://127.0.0.1:8000/`.

### 5.3. Запуск клиента воспроизведения
```powershell
cd client
python main.py
```
- Окна плеера развернутся на всех доступных мониторах.
- Нажатие `Q` (или `Й`) откроет диалог подключения к серверу.
- Нажатие `E` (или `У`) чисто закроет приложение.

---

## 6. Протокол связи WebSocket (`/ws/client`)

1. **Регистрация клиента (Client -> Server)**:
   ```json
   {
     "type": "register",
     "client_id": "pc-uuid-or-hostname",
     "hostname": "WORKSTATION-1",
     "os_info": "Windows 11",
     "screens": ["2276WM (Primary)", "TV-4K"],
     "primary_screen": "2276WM",
     "audio_enabled": true
   }
   ```
2. **Ответ сервера (Server -> Client)**:
   ```json
   {
     "type": "registered",
     "client_id": "pc-uuid-or-hostname",
     "audio_enabled": true
   }
   ```
3. **Heartbeat (Client -> Server каждые 5 сек)**:
   ```json
   {
     "type": "heartbeat",
     "client_id": "pc-uuid-or-hostname",
     "audio_enabled": true
   }
   ```
4. **Команда управления звуком (Server -> Client)**:
   ```json
   {
     "type": "set_audio",
     "audio_enabled": false,
     "enabled": false
   }
   ```
5. **Подтверждение изменения статуса (Client -> Server)**:
   ```json
   {
     "type": "status_update",
     "client_id": "pc-uuid-or-hostname",
     "audio_enabled": false
   }
   ```
