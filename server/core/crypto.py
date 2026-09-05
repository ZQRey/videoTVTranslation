"""
Модуль криптографической защиты конфиденциальных данных (паролей и секретов).
Обеспечивает симметричное шифрование (AES-128-CBC + HMAC-SHA256 через Fernet)
секретов конфигурации (паролей Active Directory, токенов) при их сохранении на диск.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("stream_server.crypto")

# Префикс зашифрованных значений в JSON конфигурации
ENCRYPTED_PREFIX = "enc:"

_cached_key: Optional[bytes] = None


def get_default_key_path() -> Path:
    """Возвращает стандартный путь к файлу мастер-ключа шифрования."""
    # Каталог конфигурации: server/config/.secret.key
    base_dir = Path(__file__).resolve().parent.parent / "config"
    return base_dir / ".secret.key"


def get_or_create_master_key(key_file_path: Optional[Path] = None) -> bytes:
    """
    Получение мастер-ключа шифрования:
    1. Переменная окружения APP_ENCRYPTION_KEY или SECRET_KEY.
    2. Файл .secret.key в папке конфигурации.
    3. Автоматическая генерация нового ключа при первом запуске.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    # 1. Проверка переменной окружения
    env_key = os.environ.get("APP_ENCRYPTION_KEY") or os.environ.get("SECRET_KEY")
    if env_key:
        try:
            # Если ключ уже в формате Fernet (urlsafe base64, 44 символа)
            raw = env_key.encode("utf-8")
            Fernet(raw)
            _cached_key = raw
            return _cached_key
        except Exception:
            # Если передана обычная строковая фраза, детерминированно получаем 32-байтный Fernet ключ
            derived = hashlib.sha256(env_key.encode("utf-8")).digest()
            _cached_key = base64.urlsafe_b64encode(derived)
            return _cached_key

    # 2. Проверка файла ключа на диске
    key_path = key_file_path or get_default_key_path()
    if key_path.exists():
        try:
            content = key_path.read_text(encoding="utf-8").strip()
            raw = content.encode("utf-8")
            Fernet(raw)
            _cached_key = raw
            return _cached_key
        except Exception as e:
            logger.warning(f"Не удалось прочитать существующий мастер-ключ из {key_path}: {e}")

    # 3. Генерация нового ключа
    new_key = Fernet.generate_key()
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(new_key.decode("utf-8"), encoding="utf-8")
        # Ограничение прав на чтение (если поддерживается ОС)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        logger.info(f"Сгенерирован новый мастер-ключ шифрования: {key_path.name}")
    except Exception as e:
        logger.error(f"Не удалось сохранить мастер-ключ на диск ({key_path}): {e}")

    _cached_key = new_key
    return _cached_key


def encrypt_secret(plaintext: str, key: Optional[bytes] = None) -> str:
    """
    Шифрует открытую строку и возвращает токен вида 'enc:<base64>'.
    Если строка уже зашифрована или пуста, возвращает ее без изменений.
    """
    if not plaintext:
        return ""
    if plaintext.startswith(ENCRYPTED_PREFIX):
        return plaintext

    master_key = key or get_or_create_master_key()
    f = Fernet(master_key)
    ciphertext = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{ciphertext}"


def decrypt_secret(ciphertext: str, key: Optional[bytes] = None) -> str:
    """
    Расшифровывает строку с префиксом 'enc:'.
    Если строка не имеет префикса 'enc:' (например, старый открытый пароль),
    возвращает ее как есть для обратной совместимости.
    """
    if not ciphertext:
        return ""
    if not ciphertext.startswith(ENCRYPTED_PREFIX):
        return ciphertext

    token = ciphertext[len(ENCRYPTED_PREFIX):]
    master_key = key or get_or_create_master_key()
    f = Fernet(master_key)

    try:
        plaintext_bytes = f.decrypt(token.encode("ascii"))
        return plaintext_bytes.decode("utf-8")
    except InvalidToken:
        logger.error("Ошибка расшифровки секрета: неверный мастер-ключ или поврежденные данные")
        return ""
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при расшифровке: {e}")
        return ""


def is_encrypted(value: Optional[str]) -> bool:
    """Проверяет, зашифровано ли переданное значение."""
    return bool(value and value.startswith(ENCRYPTED_PREFIX))
