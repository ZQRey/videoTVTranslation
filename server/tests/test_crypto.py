"""
Модульные тесты для криптографического модуля (server/core/crypto.py).
Проверяют корректность симметричного шифрования Fernet, расшифровки,
обработку граничных условий и обратную совместимость.
"""

import os
import unittest
from unittest.mock import patch

from core.crypto import (
    ENCRYPTED_PREFIX,
    decrypt_secret,
    encrypt_secret,
    get_or_create_master_key,
    is_encrypted,
)
from cryptography.fernet import Fernet


class TestCryptoModule(unittest.TestCase):
    """Тестирование шифрования и дешифрования секретных параметров."""

    def setUp(self):
        self.test_key = Fernet.generate_key()

    def test_encrypt_and_decrypt_basic(self):
        """Проверка базового цикла шифрования и расшифровки пароля."""
        password = "SuperSecretPassword123!@#"
        encrypted = encrypt_secret(password, key=self.test_key)
        self.assertTrue(encrypted.startswith(ENCRYPTED_PREFIX))
        self.assertTrue(is_encrypted(encrypted))
        self.assertNotEqual(password, encrypted)

        decrypted = decrypt_secret(encrypted, key=self.test_key)
        self.assertEqual(decrypted, password)

    def test_encrypt_unicode_cyrillic(self):
        """Шифрование строк со спецсимволами и кириллицей."""
        password = "Пароль_Админа_Домена_2026!%$^&*()_+"
        encrypted = encrypt_secret(password, key=self.test_key)
        self.assertTrue(is_encrypted(encrypted))
        decrypted = decrypt_secret(encrypted, key=self.test_key)
        self.assertEqual(decrypted, password)

    def test_empty_and_none_values(self):
        """Пустые значения и None должны обрабатываться без исключений."""
        self.assertEqual(encrypt_secret("", key=self.test_key), "")
        self.assertEqual(decrypt_secret("", key=self.test_key), "")
        self.assertEqual(encrypt_secret(None, key=self.test_key), "")
        self.assertEqual(decrypt_secret(None, key=self.test_key), "")
        self.assertFalse(is_encrypted(""))
        self.assertFalse(is_encrypted(None))

    def test_idempotent_encryption(self):
        """Повторное шифрование уже зашифрованной строки не должно создавать двойное шифрование."""
        password = "SomePassword"
        encrypted_once = encrypt_secret(password, key=self.test_key)
        encrypted_twice = encrypt_secret(encrypted_once, key=self.test_key)
        self.assertEqual(encrypted_once, encrypted_twice)

        decrypted = decrypt_secret(encrypted_twice, key=self.test_key)
        self.assertEqual(decrypted, password)

    def test_backward_compatibility_unencrypted(self):
        """Если пароль в старом конфиге не имеет префикса enc:, он возвращается как есть."""
        plain_old_password = "PlainOldPassword2024"
        self.assertFalse(is_encrypted(plain_old_password))
        decrypted = decrypt_secret(plain_old_password, key=self.test_key)
        self.assertEqual(decrypted, plain_old_password)

    def test_corrupted_token(self):
        """Поврежденный токен шифрования должен возвращать пустую строку без вылета."""
        corrupted = f"{ENCRYPTED_PREFIX}invalid_base64_garbage_token"
        self.assertEqual(decrypt_secret(corrupted, key=self.test_key), "")

    def test_wrong_key(self):
        """Попытка расшифровки чужим ключом не должна вызывать необработанный крэш."""
        other_key = Fernet.generate_key()
        encrypted = encrypt_secret("Secret", key=self.test_key)
        decrypted_with_wrong_key = decrypt_secret(encrypted, key=other_key)
        self.assertEqual(decrypted_with_wrong_key, "")

    def test_env_master_key_derivation(self):
        """Проверка детерминированного получения ключа из произвольной строковой переменной окружения."""
        with patch.dict(os.environ, {"SECRET_KEY": "arbitrary-passphrase-string"}):
            # Сброс кэша ключа
            import core.crypto as crypto_mod
            old_cache = crypto_mod._cached_key
            try:
                crypto_mod._cached_key = None
                key = get_or_create_master_key()
                self.assertIsInstance(key, bytes)
                f = Fernet(key)
                self.assertIsNotNone(f)
            finally:
                crypto_mod._cached_key = old_cache


if __name__ == "__main__":
    unittest.main()
