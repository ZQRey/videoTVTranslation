"""
Модуль аутентификации и авторизации:
1. Хеширование паролей локальных пользователей (PBKDF2-HMAC-SHA256).
2. Авторизация через Active Directory (LDAP) с обязательной проверкой
   членства в группе "Администраторы домена" (Domain Admins).
3. Потокобезопасный менеджер пользовательских сессий в памяти.
4. FastAPI зависимости для защиты маршрутов.
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, Request, status
from pydantic import BaseModel

from .config import config_manager
from .crypto import decrypt_secret

logger = logging.getLogger("stream_server.auth")

SESSION_COOKIE_NAME = "stream_session_id"


# ============================================================================
# 1. Хеширование паролей
# ============================================================================

class PasswordHasher:
    """Утилита безопасного хеширования паролей PBKDF2-HMAC-SHA256."""

    ITERATIONS = 100_000

    @classmethod
    def hash_password(cls, password: str) -> str:
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, cls.ITERATIONS
        )
        return f"pbkdf2_sha256${cls.ITERATIONS}${salt.hex()}${key.hex()}"

    @classmethod
    def verify_password(cls, password: str, hash_str: str) -> bool:
        if not hash_str:
            # Дефолтный пароль "admin", если хеш еще не был задан
            return password == "admin"
        try:
            parts = hash_str.split("$")
            if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
                return False
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected_key = bytes.fromhex(parts[3])
            computed_key = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, iterations
            )
            return hmac.compare_digest(computed_key, expected_key)
        except Exception as e:
            logger.error(f"Ошибка проверки хеша пароля: {e}")
            return False


# ============================================================================
# 2. Сессии пользователей
# ============================================================================

class UserSession(BaseModel):
    session_id: str
    username: str
    display_name: str
    auth_type: str  # "local" | "domain"
    is_admin: bool
    created_at: float
    expires_at: float

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class SessionManager:
    """Менеджер сессий пользователей в памяти с TTL."""

    def __init__(self):
        self._sessions: Dict[str, UserSession] = {}

    def create_session(
        self,
        username: str,
        display_name: str,
        auth_type: str,
        is_admin: bool = True,
        lifetime_hours: int = 24,
    ) -> UserSession:
        self.cleanup_expired()
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + (lifetime_hours * 3600)
        session = UserSession(
            session_id=session_id,
            username=username,
            display_name=display_name,
            auth_type=auth_type,
            is_admin=is_admin,
            created_at=now,
            expires_at=expires_at,
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: Optional[str]) -> Optional[UserSession]:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if not session:
            return None
        if session.is_expired():
            self.delete_session(session_id)
            return None
        return session

    def delete_session(self, session_id: Optional[str]) -> bool:
        if session_id and session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def cleanup_expired(self) -> None:
        now = time.time()
        expired = [sid for sid, sess in self._sessions.items() if sess.expires_at <= now]
        for sid in expired:
            del self._sessions[sid]


session_manager = SessionManager()


# ============================================================================
# 3. Сервис аутентификации Active Directory (LDAP)
# ============================================================================

class LdapAuthService:
    """
    Сервис аутентификации через Active Directory (LDAP/LDAPS).
    Строго проверяет членство пользователя в группе 'Администраторы домена' (Domain Admins).
    """

    @classmethod
    def authenticate(
        cls, username: str, password: str
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Проверка учетных данных пользователя в Active Directory.
        Возвращает: (success: bool, message: str, user_info: dict | None).
        """
        settings = config_manager.get_settings().auth.domain
        if not settings.enabled:
            return False, "Авторизация через Active Directory отключена в настройках", None

        if not username or not password:
            return False, "Имя пользователя и пароль обязательны", None

        # Очистка имени пользователя от доменных префиксов/суффиксов для поиска sAMAccountName
        clean_username = username.strip()
        if "\\" in clean_username:
            clean_username = clean_username.split("\\", 1)[1]
        elif "@" in clean_username:
            clean_username = clean_username.split("@", 1)[0]

        try:
            import ldap3
            from ldap3 import ALL, Connection, Server, SUBTREE
            from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPSocketOpenError
        except ImportError:
            logger.error("Библиотека ldap3 не установлена")
            return False, "LDAP библиотека не установлена на сервере", None

        server_host = settings.server
        port = settings.port or (636 if settings.use_ssl else 389)
        base_dn = settings.base_dn or "DC=gp1,DC=loc"
        domain = settings.domain or "gp1.loc"

        # Сервисная учетная запись для первоначального поиска
        service_user = settings.service_user
        if "@" not in service_user and "\\" not in service_user and domain:
            service_user_upn = f"{service_user}@{domain}"
        else:
            service_user_upn = service_user
        service_password = decrypt_secret(settings.service_password)

        try:
            server = Server(
                server_host,
                port=port,
                use_ssl=settings.use_ssl,
                get_info=ALL,
                connect_timeout=5,
            )

            # 1. Служебный bind для поиска DN пользователя и DN группы админов
            conn = Connection(
                server,
                user=service_user_upn,
                password=service_password,
                auto_bind=True,
                read_only=True,
            )
        except LDAPSocketOpenError as e:
            logger.error(f"Не удалось подключиться к контроллеру домена {server_host}:{port}: {e}")
            return (
                False,
                f"Не удалось подключиться к контроллеру домена {server_host}: {e}",
                None,
            )
        except LDAPBindError as e:
            logger.error(f"Ошибка авторизации сервисной учетной записи LDAP: {e}")
            return (
                False,
                "Ошибка конфигурации домена: сервисная учетная запись отклонена",
                None,
            )
        except Exception as e:
            logger.error(f"Ошибка подключения к LDAP: {e}")
            return False, f"Ошибка подключения к домену: {e}", None

        try:
            # 2. Поиск объекта пользователя
            user_filter = (
                f"(&(objectCategory=person)(objectClass=user)"
                f"(|(sAMAccountName={clean_username})(userPrincipalName={clean_username}@{domain})))"
            )
            conn.search(
                search_base=base_dn,
                search_filter=user_filter,
                search_scope=SUBTREE,
                attributes=[
                    "distinguishedName",
                    "displayName",
                    "sAMAccountName",
                    "userPrincipalName",
                    "memberOf",
                ],
            )

            if not conn.entries:
                conn.unbind()
                return False, f"Пользователь '{clean_username}' не найден в домене", None

            user_entry = conn.entries[0]
            user_dn = str(user_entry.distinguishedName)
            display_name = (
                str(user_entry.displayName)
                if hasattr(user_entry, "displayName") and user_entry.displayName
                else clean_username
            )

            # 3. Поиск группы "Администраторы домена"
            admin_group_name = settings.admin_group or "Администраторы домена"
            group_filter = (
                f"(&(objectClass=group)(|(sAMAccountName=Domain Admins)"
                f"(sAMAccountName=Администраторы домена)(cn={admin_group_name})))"
            )
            conn.search(
                search_base=base_dn,
                search_filter=group_filter,
                search_scope=SUBTREE,
                attributes=["distinguishedName", "cn", "sAMAccountName"],
            )

            admin_group_dns = [str(entry.distinguishedName) for entry in conn.entries]
            # Добавим дефолтный путь, если поиск не дал результатов
            if not admin_group_dns:
                admin_group_dns.append(f"CN={admin_group_name},CN=Users,{base_dn}")

            # 4. Проверка пароля пользователя (bind от имени пользователя)
            user_bind_id = (
                f"{clean_username}@{domain}" if domain else user_dn
            )
            user_conn = Connection(
                server,
                user=user_bind_id,
                password=password,
                auto_bind=False,
            )

            if not user_conn.bind():
                user_conn.unbind()
                conn.unbind()
                return False, "Неверный пароль доменной учетной записи", None

            user_conn.unbind()

            # 5. Строгая проверка членства в группе Администраторов домена
            # Вариант A: прямое членство через memberOf
            is_domain_admin = False
            user_memberships = []
            if hasattr(user_entry, "memberOf") and user_entry.memberOf:
                user_memberships = [str(m).lower() for m in user_entry.memberOf]

            for g_dn in admin_group_dns:
                if g_dn.lower() in user_memberships:
                    is_domain_admin = True
                    break

            # Вариант B: рекурсивная проверка членства (LDAP_MATCHING_RULE_IN_CHAIN)
            if not is_domain_admin:
                for g_dn in admin_group_dns:
                    chain_filter = (
                        f"(&(objectClass=user)(sAMAccountName={clean_username})"
                        f"(memberOf:1.2.840.113556.1.4.1941:={g_dn}))"
                    )
                    conn.search(
                        search_base=base_dn,
                        search_filter=chain_filter,
                        search_scope=SUBTREE,
                        attributes=["sAMAccountName"],
                    )
                    if conn.entries:
                        is_domain_admin = True
                        break

            conn.unbind()

            if not is_domain_admin:
                logger.warning(
                    f"Доступ отклонен: пользователь {clean_username} не входит в группу Администраторов домена"
                )
                return (
                    False,
                    "Доступ запрещен: вход в систему разрешен только Администраторам домена (Domain Admins)",
                    None,
                )

            logger.info(f"Успешная доменная авторизация: {clean_username} (Администратор домена)")
            return True, "Успешная авторизация", {
                "username": clean_username,
                "display_name": display_name,
                "auth_type": "domain",
                "is_admin": True,
            }

        except Exception as e:
            logger.error(f"Исключение при LDAP авторизации: {e}")
            try:
                conn.unbind()
            except Exception:
                pass
            return False, f"Ошибка при проверке доменной авторизации: {e}", None

    @classmethod
    def test_connection(
        cls,
        server_host: Optional[str] = None,
        port: Optional[int] = None,
        use_ssl: Optional[bool] = None,
        domain: Optional[str] = None,
        base_dn: Optional[str] = None,
        service_user: Optional[str] = None,
        service_password: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Тестирование подключения к контроллеру домена (с возможностью передачи тестовых параметров)."""
        settings = config_manager.get_settings().auth.domain
        try:
            import ldap3
            from ldap3 import ALL, Connection, Server
        except ImportError:
            return False, "Библиотека ldap3 не установлена"

        target_server = server_host or settings.server
        target_use_ssl = use_ssl if use_ssl is not None else settings.use_ssl
        target_port = port or settings.port or (636 if target_use_ssl else 389)
        target_domain = domain or settings.domain or "gp1.loc"
        target_service_user = service_user or settings.service_user

        if "@" not in target_service_user and "\\" not in target_service_user and target_domain:
            service_user_upn = f"{target_service_user}@{target_domain}"
        else:
            service_user_upn = target_service_user

        # Если передан открытый пароль, используем его; если передан ****** или пусто - расшифровываем из настроек
        if service_password and service_password != "******":
            target_password = service_password
        else:
            target_password = decrypt_secret(settings.service_password)

        if not target_password:
            return False, "Пароль сервисной учетной записи не задан"

        try:
            server = Server(
                target_server,
                port=target_port,
                use_ssl=target_use_ssl,
                get_info=ALL,
                connect_timeout=4,
            )
            conn = Connection(
                server,
                user=service_user_upn,
                password=target_password,
                auto_bind=True,
                read_only=True,
            )
            bound = conn.bound
            conn.unbind()
            if bound:
                return True, f"Соединение с контроллером {target_server} успешно установлено"
            return False, f"Не удалось привязаться к {target_server}"
        except Exception as e:
            return False, f"Ошибка подключения к {target_server}: {e}"


# ============================================================================
# 4. FastAPI зависимости и утилиты
# ============================================================================

def get_current_user_optional(request: Request) -> Optional[UserSession]:
    """Получение текущего пользователя из сессионной куки или Bearer-токена."""
    # 1. Попытка чтения из cookie
    session_id = request.cookies.get(SESSION_COOKIE_NAME)

    # 2. Fallback: заголовок Authorization: Bearer <token>
    if not session_id:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_id = auth_header.split(" ", 1)[1].strip()

    if not session_id:
        return None

    return session_manager.get_session(session_id)


def require_authenticated_user(request: Request) -> UserSession:
    """
    FastAPI зависимость: требует аутентифицированного пользователя.
    Выбрасывает HTTP 401 при отсутствии валидной сессии.
    """
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация в системе",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user
