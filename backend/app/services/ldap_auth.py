"""LDAP/AD-compatible authentication boundary.

The adapter is only enabled when ``TGOAT_AUTH_PROVIDER=ldap``.  It never writes
into the directory: bind/search is read-only and application authorization is
still evaluated from the mapped roles/grants plus PostgreSQL assignments.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import hashlib
import hmac
import uuid
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from typing import Iterable

from backend.app.services.auth import AuthUser, DemoAuthProvider, ROLE_CAPABILITIES, _b64encode

logger = logging.getLogger(__name__)


class LDAPConfigurationError(RuntimeError):
    pass


def _json_env(name: str, default):
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise LDAPConfigurationError(f"{name} must be valid JSON") from error


class LDAPAuthProvider(DemoAuthProvider):
    """Authenticate against any LDAPv3 directory and map groups to capabilities.

    A service bind is optional.  If omitted, anonymous search must be permitted.
    The group map is explicit; an unknown directory group grants nothing.
    """

    def __init__(self, *, url: str, base_dn: str, user_filter: str = "(uid={username})",
                 bind_dn: str | None = None, bind_password: str | None = None,
                 group_map: dict | None = None, token_secret: str | None = None,
                 ca_file: str | None = None, insecure_local_test: bool = False):
        parsed = urlparse(url)
        if parsed.scheme not in {'ldap', 'ldaps'} or not parsed.hostname or not base_dn:
            raise LDAPConfigurationError('LDAP URL must use ldap:// or ldaps:// and base DN is required')
        if user_filter.count('{username}') != 1 or user_filter.count('{') != 1 or user_filter.count('}') != 1:
            raise LDAPConfigurationError('LDAP user filter must contain exactly one {username} placeholder')
        if insecure_local_test and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise LDAPConfigurationError('Unencrypted LDAP is allowed only for localhost test hosts')
        secret = token_secret or os.getenv('DEMO_AUTH_TOKEN_SECRET', '')
        if len(secret) < 32:
            raise LDAPConfigurationError('LDAP sessions require a strong random token secret (>=32 chars)')
        super().__init__(accounts=[], token_secret=secret)
        self.url = url
        self.base_dn = base_dn
        self.user_filter = user_filter
        self.bind_dn = bind_dn
        self.bind_password = bind_password or ""
        self.group_map = group_map or {}
        self.ca_file = ca_file
        self.insecure_local_test = insecure_local_test
        self._parsed_url = parsed
        self._cache: dict[str, AuthUser] = {}

    @classmethod
    def from_environment(cls) -> "LDAPAuthProvider":
        return cls(
            url=os.getenv("TGOAT_LDAP_URL", ""),
            base_dn=os.getenv("TGOAT_LDAP_BASE_DN", ""),
            user_filter=os.getenv("TGOAT_LDAP_USER_FILTER", "(uid={username})"),
            bind_dn=os.getenv("TGOAT_LDAP_BIND_DN") or None,
            bind_password=os.getenv("TGOAT_LDAP_BIND_PASSWORD") or None,
            group_map=_json_env("TGOAT_LDAP_GROUP_MAP_JSON", {}),
            token_secret=os.getenv("DEMO_AUTH_TOKEN_SECRET"),
            ca_file=os.getenv('TGOAT_LDAP_CA_FILE') or None,
            insecure_local_test=os.getenv('TGOAT_LDAP_INSECURE_LOCAL_TEST', '0') == '1',
        )

    @staticmethod
    def _ldap3():
        try:
            import ldap3
        except ImportError as error:
            raise LDAPConfigurationError("ldap3 dependency is required for LDAP mode") from error
        return ldap3

    def _server(self):
        ldap3 = self._ldap3()
        encrypted = self._parsed_url.scheme == 'ldaps'
        return ldap3.Server(self._parsed_url.hostname,
                            port=self._parsed_url.port or (636 if encrypted else 389),
                            use_ssl=encrypted, get_info=ldap3.NONE, connect_timeout=5,
                            tls=ldap3.Tls(validate=ssl.CERT_REQUIRED,
                                          ca_certs_file=self.ca_file))

    def _secure_bind(self, user=None, password=None):
        ldap3 = self._ldap3()
        connection = ldap3.Connection(self._server(), user=user, password=password,
                                      auto_bind=False, receive_timeout=5)
        try:
            # ldap3.Connection.open() returns None on success; inspect the
            # connection state instead of treating its return value as bool.
            connection.open()
            if connection.closed:
                raise LDAPConfigurationError('LDAP transport unavailable')
            if (self._parsed_url.scheme == 'ldap' and not self.insecure_local_test
                    and not connection.start_tls()):
                raise LDAPConfigurationError('LDAP STARTTLS is required')
            if not connection.bind():
                connection.unbind()
                return None
            return connection
        except Exception:
            try:
                connection.unbind()
            except Exception:
                pass
            raise

    def _service_connection(self):
        return self._secure_bind(self.bind_dn, self.bind_password)

    def _lookup(self, username: str) -> tuple[str, list[str]] | None:
        ldap3 = self._ldap3()
        safe_username = ldap3.utils.conv.escape_filter_chars(username)
        query = self.user_filter.format(username=safe_username)
        connection = self._service_connection()
        if connection is None:
            return None
        with connection:
            ok = connection.search(self.base_dn, query, search_scope=ldap3.SUBTREE,
                                   attributes=["uid", "sAMAccountName", "cn", "memberOf"], size_limit=2)
            if not ok or len(connection.entries) != 1:
                return None
            entry = connection.entries[0]
            dn = str(entry.entry_dn)
            groups = [str(value) for value in getattr(entry, "memberOf", []).values] if hasattr(
                getattr(entry, "memberOf", None), "values") else []
            # OpenLDAP often has no memberOf overlay. Read group memberships by member DN.
            if not groups:
                connection.search(self.base_dn,
                                  f"(&(objectClass=groupOfNames)(member={ldap3.utils.conv.escape_filter_chars(dn)}))",
                                  search_scope=ldap3.SUBTREE, attributes=["cn"], size_limit=100)
                groups = [str(item.entry_dn) for item in connection.entries]
            return dn, groups

    def _mapped_user(self, username: str, groups: Iterable[str]) -> AuthUser:
        roles: list[str] = []
        grants: list[str] = []
        roots: list[int] = []
        normalized_groups = tuple(dict.fromkeys(str(group) for group in groups))
        for group in normalized_groups:
            cn = group.split(",", 1)[0].split("=", 1)[-1]
            spec = self.group_map.get(group, self.group_map.get(cn))
            if spec is None:
                continue
            if isinstance(spec, str):
                spec = {"roles": [spec]}
            for role in spec.get('roles', ()):
                if role not in ROLE_CAPABILITIES:
                    raise LDAPConfigurationError('LDAP group map contains unknown role')
                roles.append(role)
            grants.extend(str(grant) for grant in spec.get("grants", ()))
            roots.extend(int(root) for root in spec.get("scope_roots", ()))
        roles = list(dict.fromkeys(roles))
        grants = list(dict.fromkeys(grants))
        if not roles and not grants:
            # Authentication without mapped authorization must fail closed.
            raise LDAPConfigurationError("LDAP user has no mapped application role/grant")
        global_scope = "central_dispatcher" in roles
        display = next((role for role in ("central_dispatcher", "district_dispatcher", "technician",
                                           "system_administrator") if role in roles),
                       "manager" if "management.read" in grants else
                       "analyst" if "evidence.read" in grants else "user")
        return AuthUser(username, username, display, None if global_scope else tuple(dict.fromkeys(roots)),
                        tuple(roles), tuple(grants), normalized_groups)

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        if not username or not password:
            return None
        try:
            found = self._lookup(username)
        except Exception as error:
            logger.warning("LDAP user lookup failed; authentication denied (%s)", type(error).__name__)
            return None
        if found is None:
            return None
        dn, groups = found
        try:
            connection = self._secure_bind(user=dn, password=password)
        except Exception:
            return None
        if connection is None:
            return None
        connection.unbind()
        try:
            user = self._mapped_user(username, groups)
        except LDAPConfigurationError:
            return None
        self._cache[username] = user
        return user

    def issue_token(self, user: AuthUser) -> str:
        expires = int((datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp())
        payload = _b64encode(json.dumps({'sub': user.user_id, 'exp': expires,
                                         'jti': uuid.uuid4().hex}, separators=(',', ':')).encode())
        signature = _b64encode(hmac.new(self._secret, payload.encode(), hashlib.sha256).digest())
        return f'{payload}.{signature}'

    def resolve_token(self, token: str) -> AuthUser | None:
        claims = self._verified_claims(token)
        if not claims:
            return None
        try:
            import time
            if int(claims["exp"]) <= int(time.time()):
                return None
            username = str(claims["sub"])
        except (KeyError, TypeError, ValueError):
            return None
        try:
            found = self._lookup(username)
        except Exception:
            return None  # Directory outage is denied, never demo fallback.
        if found is None:
            return None
        try:
            user = self._mapped_user(username, found[1])
        except LDAPConfigurationError:
            return None
        self._cache[username] = user
        return user

    def directory_users(self) -> tuple[AuthUser, ...]:
        # Deliberately no whole-directory enumeration requirement. Users appear
        # after authentication; authorization administration lives in PostgreSQL.
        return tuple(self._cache.values())
