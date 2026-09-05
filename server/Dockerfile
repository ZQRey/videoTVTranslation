# Базовый образ с Python 3.11 на базе Debian slim
FROM python:3.11-slim

# Установка системных зависимостей: ffmpeg, tzdata, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    tzdata \
    procps \
    adb \
    && rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода проекта
COPY config/ ./config/
COPY core/ ./core/
COPY static/ ./static/
COPY templates/ ./templates/
COPY plugins_custom/ ./plugins_custom/
COPY main.py .
COPY run.py .

# Создание служебных директорий
RUN mkdir -p /app/media /app/logs /app/config /app/plugins_custom

# Открытие порта веб-панели управления
EXPOSE 8000

# Переменные окружения по умолчанию для работы внутри Docker сети
ENV PYTHONUNBUFFERED=1 \
    RTSP_TARGET_URL=rtsp://mediamtx:8554/live \
    MEDIAMTX_HLS_URL=http://localhost:8888/live \
    MEDIA_DIR=/app/media \
    WEB_PORT=8000

# Запуск приложения
CMD ["python", "run.py"]
