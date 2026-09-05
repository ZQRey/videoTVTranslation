"""
Модульные и интеграционные тесты для системы аутентификации и авторизации:
1. Хеширование и проверка паролей (PasswordHasher).
2. Управление сессиями (SessionManager).
3. Сервис Active Directory (LdapAuthService) с валидацией прав Администраторов домена.
4. Интеграционные ASGI тесты защиты маршрутов (без сторонних зависимостей).
"""

import json
import time
import unittest
from unittest.mock import MagicMock, patch

from core.auth import (
    SESSION_COOKIE_NAME,
    LdapAuthService,
    PasswordHasher,
    SessionManager,
    session_manager,
)
from core.config import config_manager
from main import (
    ChangePasswordRequest,
    LoginRequest,
    api_change_password,
    api_get_current_user,
    api_login,
    api_logout,
    app,
)


class TestPasswordHasher(unittest.TestCase):
    """Тестирование хеширования и валидации паролей."""

    def test_hash_and_verify(self):
        password = "SuperSecretPassword123"
        pwd_hash = PasswordHasher.hash_password(password)
        self.assertTrue(pwd_hash.startswith("pbkdf2_sha256$"))
        self.assertTrue(PasswordHasher.verify_password(password, pwd_hash))
        self.assertFalse(PasswordHasher.verify_password("WrongPassword", pwd_hash))

    def test_default_empty_hash(self):
        # При пустом хеше по умолчанию проверяется пароль "admin"
        self.assertTrue(PasswordHasher.verify_password("admin", ""))
        self.assertFalse(PasswordHasher.verify_password("other", ""))

    def test_corrupted_hash(self):
        self.assertFalse(PasswordHasher.verify_password("admin", "invalid_format"))
        self.assertFalse(PasswordHasher.verify_password("admin", "pbkdf2_sha256$bad$hex$data"))


class TestSessionManager(unittest.TestCase):
    """Тестирование жизненного цикла сессий."""

    def setUp(self):
        self.sm = SessionManager()

    def test_create_and_get_session(self):
        sess = self.sm.create_session(
            username="testuser",
            display_name="Test User",
            auth_type="local",
            is_admin=True,
            lifetime_hours=1,
        )
        self.assertIsNotNone(sess.session_id)
        self.assertEqual(sess.username, "testuser")

        fetched = self.sm.get_session(sess.session_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.username, "testuser")

    def test_delete_session(self):
        sess = self.sm.create_session(
            username="admin", display_name="Admin", auth_type="local"
        )
        self.assertTrue(self.sm.delete_session(sess.session_id))
        self.assertIsNone(self.sm.get_session(sess.session_id))

    def test_expired_session(self):
        sess = self.sm.create_session(
            username="timed", display_name="Timed", auth_type="local", lifetime_hours=0
        )
        # Искусственно сдвигаем expires_at в прошлое
        sess.expires_at = time.time() - 10
        self.assertIsNone(self.sm.get_session(sess.session_id))


class TestLdapAuthService(unittest.TestCase):
    """Тестирование логики авторизации через Active Directory."""

    @patch("ldap3.Connection")
    @patch("ldap3.Server")
    def test_domain_admin_success(self, mock_server, mock_connection):
        # 1. Mock service connection
        mock_conn_instance = MagicMock()
        mock_conn_instance.bound = True
        mock_connection.return_value = mock_conn_instance

        # Mock user entry
        mock_user = MagicMock()
        mock_user.distinguishedName = "CN=Test Admin,OU=IT,DC=gp1,DC=loc"
        mock_user.displayName = "Test Administrator"
        mock_user.sAMAccountName = "test_admin"
        mock_user.memberOf = [
            "CN=Администраторы домена,CN=Users,DC=gp1,DC=loc",
            "CN=Domain Users,CN=Users,DC=gp1,DC=loc",
        ]
        mock_conn_instance.entries = [mock_user]

        # 2. Mock user bind connection
        user_bind_conn = MagicMock()
        user_bind_conn.bind.return_value = True

        # mock_connection returns mock_conn_instance first, then user_bind_conn
        mock_connection.side_effect = [mock_conn_instance, user_bind_conn]

        success, msg, user_info = LdapAuthService.authenticate("test_admin", "CorrectPassword")
        self.assertTrue(success)
        self.assertIsNotNone(user_info)
        self.assertEqual(user_info["username"], "test_admin")
        self.assertEqual(user_info["auth_type"], "domain")
        self.assertTrue(user_info["is_admin"])

    @patch("ldap3.Connection")
    @patch("ldap3.Server")
    def test_domain_non_admin_denied(self, mock_server, mock_connection):
        """Пользователь с верным паролем, но НЕ входящий в Администраторы домена, должен быть отклонен."""
        mock_conn_instance = MagicMock()
        mock_conn_instance.bound = True

        # Mock user without Domain Admins group
        mock_user = MagicMock()
        mock_user.distinguishedName = "CN=Regular User,OU=Staff,DC=gp1,DC=loc"
        mock_user.displayName = "Regular User"
        mock_user.sAMAccountName = "regular_user"
        mock_user.memberOf = ["CN=Пользователи домена,CN=Users,DC=gp1,DC=loc"]

        # First search returns user
        mock_conn_instance.entries = [mock_user]

        # User bind succeeds
        user_bind_conn = MagicMock()
        user_bind_conn.bind.return_value = True

        # Chain search returns empty entries (not an admin)
        def search_side_effect(*args, **kwargs):
            search_filter = kwargs.get("search_filter", "")
            if "1.2.840.113556.1.4.1941" in search_filter:
                mock_conn_instance.entries = []
            elif "objectClass=group" in search_filter:
                group_entry = MagicMock()
                group_entry.distinguishedName = "CN=Администраторы домена,CN=Users,DC=gp1,DC=loc"
                mock_conn_instance.entries = [group_entry]

        mock_conn_instance.search.side_effect = search_side_effect
        mock_connection.side_effect = [mock_conn_instance, user_bind_conn]

        success, msg, user_info = LdapAuthService.authenticate("regular_user", "ValidPassword")
        self.assertFalse(success)
        self.assertIn("только Администраторам домена", msg)
        self.assertIsNone(user_info)


async def make_asgi_request(
    app,
    method: str,
    path: str,
    headers: dict = None,
    body: bytes = b"",
):
    """Вызов ASGI-приложения FastAPI без внешних HTTP библиотек."""
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode("latin1"), v.encode("latin1")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
    }

    response_status = 200
    response_headers = []
    response_body = bytearray()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal response_status, response_headers, response_body
        if message["type"] == "http.response.start":
            response_status = message["status"]
            response_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            response_body.extend(message.get("body", b""))

    await app(scope, receive, send)
    hdrs = dict((k.decode("latin1"), v.decode("latin1")) for k, v in response_headers)
    return response_status, hdrs, bytes(response_body)


class TestFastAPIAuthIntegration(unittest.IsolatedAsyncioTestCase):
    """Интеграционные тесты HTTP запросов и защиты маршрутов через ASGI."""

    async def test_unauthenticated_dashboard_redirects_to_login(self):
        status, headers, body = await make_asgi_request(app, "GET", "/")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("location"), "/login")

    async def test_login_page_renders(self):
        status, headers, body = await make_asgi_request(app, "GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        text = body.decode("utf-8")
        self.assertIn("StreamServer", text)
        self.assertIn("Active Directory", text)

    async def test_unauthenticated_api_returns_401(self):
        status, headers, body = await make_asgi_request(app, "GET", "/api/status")
        self.assertEqual(status, 401)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("Требуется авторизация", data.get("detail", ""))

    async def test_login_and_access_flow(self):
        # 1. Попытка входа с неверным паролем
        payload_bad = json.dumps(
            {"username": "admin", "password": "WrongPassword", "auth_type": "local"}
        ).encode()
        status, headers, body = await make_asgi_request(
            app,
            "POST",
            "/api/auth/login",
            headers={"Content-Type": "application/json"},
            body=payload_bad,
        )
        self.assertEqual(status, 401)

        # 2. Успешный вход с дефолтным паролем
        payload_good = json.dumps(
            {"username": "admin", "password": "admin", "auth_type": "local"}
        ).encode()
        status, headers, body = await make_asgi_request(
            app,
            "POST",
            "/api/auth/login",
            headers={"Content-Type": "application/json"},
            body=payload_good,
        )
        self.assertEqual(status, 200)
        self.assertIn("set-cookie", headers)
        cookie_header = headers["set-cookie"]
        self.assertIn(SESSION_COOKIE_NAME, cookie_header)

        # Извлекаем session_id из Set-Cookie
        session_cookie = cookie_header.split(";")[0]

        # 3. Доступ к защищенному дашборду с кукой
        status, headers, body = await make_asgi_request(
            app, "GET", "/", headers={"Cookie": session_cookie}
        )
        self.assertEqual(status, 200)

        # 4. Доступ к защищенному API с кукой
        status, headers, body = await make_asgi_request(
            app, "GET", "/api/status", headers={"Cookie": session_cookie}
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("streamer", data)

        # 5. Проверка эндпоинта /api/auth/me
        status, headers, body = await make_asgi_request(
            app, "GET", "/api/auth/me", headers={"Cookie": session_cookie}
        )
        self.assertEqual(status, 200)
        me_data = json.loads(body.decode("utf-8"))
        self.assertEqual(me_data.get("username"), "admin")

        # 6. Смена пароля
        change_pwd_payload = json.dumps(
            {"old_password": "admin", "new_password": "NewSecretPassword123"}
        ).encode()
        status, headers, body = await make_asgi_request(
            app,
            "POST",
            "/api/auth/change-password",
            headers={"Content-Type": "application/json", "Cookie": session_cookie},
            body=change_pwd_payload,
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body.decode("utf-8")).get("success"))

        # 7. Выход из системы
        status, headers, body = await make_asgi_request(
            app, "POST", "/api/auth/logout", headers={"Cookie": session_cookie}
        )
        self.assertEqual(status, 200)

        # 8. После выхода старый пароль не подходит
        status, headers, body = await make_asgi_request(
            app,
            "POST",
            "/api/auth/login",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"username": "admin", "password": "admin", "auth_type": "local"}).encode(),
        )
        self.assertEqual(status, 401)

        # 9. Новый пароль подходит
        status, headers, body = await make_asgi_request(
            app,
            "POST",
            "/api/auth/login",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"username": "admin", "password": "NewSecretPassword123", "auth_type": "local"}).encode(),
        )
        self.assertEqual(status, 200)
        new_session_cookie = headers["set-cookie"].split(";")[0]

        # Возвращаем пароль обратно в default
        reset_pwd_payload = json.dumps(
            {"old_password": "NewSecretPassword123", "new_password": "admin"}
        ).encode()
        await make_asgi_request(
            app,
            "POST",
            "/api/auth/change-password",
            headers={"Content-Type": "application/json", "Cookie": new_session_cookie},
            body=reset_pwd_payload,
        )

        # 10. Проверка эндпоинта /api/auth/test-ad
        with patch.object(LdapAuthService, "test_connection", return_value=(True, "OK")):
            status, headers, body = await make_asgi_request(
                app, "POST", "/api/auth/test-ad", headers={"Cookie": new_session_cookie}
            )
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body.decode("utf-8")).get("success"))


if __name__ == "__main__":
    unittest.main()
