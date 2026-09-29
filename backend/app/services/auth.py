"""Replaceable authentication boundary with a deliberately demo-only provider."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    username: str
    role: str
    scope_roots: tuple[int, ...] | None
    roles: tuple[str, ...] = ()
    grants: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()

    def __post_init__(self):
        roles = tuple(dict.fromkeys(self.roles or ((self.role,) if self.role in ROLE_CAPABILITIES else ())))
        derived = {capability for role in roles for capability in ROLE_CAPABILITIES.get(role, ())}
        grants = tuple(dict.fromkeys((*derived, *self.grants)))
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "grants", grants)
        object.__setattr__(self, "groups", tuple(dict.fromkeys(self.groups)))
        if self.scope_roots is not None:
            object.__setattr__(self, "scope_roots", tuple(dict.fromkeys(int(root) for root in self.scope_roots)))

    def has_role(self, *roles: str) -> bool:
        return bool(set(roles).intersection(self.roles))

    def has_capability(self, *capabilities: str) -> bool:
        return bool(set(capabilities).intersection(self.grants))


ROLE_CAPABILITIES = {
    "central_dispatcher": (
        "objects.read", "management.read", "evidence.read", "signals.read", "signals.decide",
        "requests.read", "requests.create", "requests.edit", "requests.assign", "requests.dispatch",
        "prediction.run", "prediction.close", "prediction.review", "prediction.outcome", "notifications.read", "notifications.update",
        "audit.read", "replay.read", "replay.control",
    ),
    "district_dispatcher": (
        "objects.read", "management.read", "evidence.read", "signals.read", "signals.decide",
        "requests.read", "requests.create", "requests.edit", "requests.assign", "requests.dispatch",
        "prediction.run", "prediction.close", "prediction.review", "prediction.outcome", "notifications.read", "notifications.update",
        "audit.read", "replay.read", "replay.control",
    ),
    "technician": (
        "objects.read", "evidence.read", "requests.read", "requests.create", "tasks.execute", "prediction.review",
        "notifications.read", "notifications.update", "replay.read",
    ),
    "system_administrator": ("access.manage", "audit.read"),
}

PERSONA_GRANTS = {"management.read", "evidence.read", "model.explain"}


class AuthProvider(Protocol):
    def authenticate(self, username: str, password: str) -> AuthUser | None: ...
    def issue_token(self, user: AuthUser) -> str: ...
    def token_expiration(self, token: str) -> datetime | None: ...
    def resolve_token(self, token: str) -> AuthUser | None: ...
    def directory_users(self) -> tuple[AuthUser, ...]: ...


_DEMO_ACCOUNTS = (
    {"username": "tech-28", "password": "tech-demo", "role": "technician", "scope_roots": [28], "groups": ["complex:28"]},
    {"username": "tech-9", "password": "tech-demo-2", "role": "technician", "scope_roots": [9], "groups": ["complex:9"]},
    {"username": "dispatch-9", "password": "dispatch-demo", "role": "district_dispatcher", "scope_roots": [9], "groups": ["district:9"]},
    {"username": "dispatch-6", "password": "dispatch-demo-2", "role": "district_dispatcher", "scope_roots": [6], "groups": ["district:6"]},
    {"username": "tech-20", "password": "tech-demo-20", "role": "technician", "scope_roots": [20], "groups": ["complex:20"]},
    {"username": "central", "password": "central-demo", "role": "central_dispatcher", "scope_roots": None},
    {"username": "is-admin", "password": "admin-demo", "role": "system_administrator", "scope_roots": [], "roles": ["system_administrator"]},
    {"username": "manager-demo", "password": "manager-demo", "role": "manager", "scope_roots": [9], "groups": ["district:9"], "grants": ["management.read"]},
    {"username": "analyst-demo", "password": "analyst-demo", "role": "analyst", "scope_roots": [9], "groups": ["district:9"], "grants": ["evidence.read", "model.explain"]},
    {"username": "dual-demo", "password": "dual-demo", "role": "district_dispatcher",
     "roles": ["district_dispatcher", "technician"], "scope_roots": [9, 28],
     "groups": ["district:9", "complex:28"]},
)
_TOKEN_SECRET = os.getenv("DEMO_AUTH_TOKEN_SECRET", "demo-only-change-before-sharing")
_TOKEN_TTL = timedelta(hours=8)


def _configured_accounts() -> tuple[dict, ...]:
    extra = os.getenv("DEMO_AUTH_USERS_JSON")
    if not extra:
        return _DEMO_ACCOUNTS
    try:
        accounts = json.loads(extra)
        if not isinstance(accounts, list):
            raise ValueError("DEMO_AUTH_USERS_JSON must be a JSON array")
        return (*_DEMO_ACCOUNTS, *accounts)
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError("Invalid DEMO_AUTH_USERS_JSON") from error


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class DemoAuthProvider:
    """In-memory demo users; inject another AuthProvider for LDAP/AD later."""

    def __init__(self, accounts: tuple[dict, ...] | list[dict] | None = None,
                 token_secret: str = _TOKEN_SECRET):
        self._secret = token_secret.encode("utf-8")
        self._users: dict[str, tuple[AuthUser, bytes, bytes]] = {}
        for account in (accounts if accounts is not None else _configured_accounts()):
            salt = secrets.token_bytes(16)
            role = account["role"]
            configured_roles = account.get("roles")
            roles = tuple(configured_roles) if configured_roles is not None else ((role,) if role in ROLE_CAPABILITIES else ())
            user = AuthUser(account["username"], account["username"], role,
                            tuple(account["scope_roots"]) if account["scope_roots"] is not None else None,
                            roles, tuple(account.get("grants", ())), tuple(account.get("groups", ())))
            digest = hashlib.pbkdf2_hmac("sha256", account["password"].encode(), salt, 180_000)
            self._users[user.username] = (user, salt, digest)

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        record = self._users.get(username)
        if record is None:
            hashlib.pbkdf2_hmac("sha256", password.encode(), b"unknown-demo-user", 180_000)
            return None
        user, salt, expected = record
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 180_000)
        return user if hmac.compare_digest(actual, expected) else None

    def directory_users(self) -> tuple[AuthUser, ...]:
        """Public identity capability; never exposes credential storage."""
        return tuple(record[0] for record in self._users.values())

    def issue_token(self, user: AuthUser) -> str:
        expires = int((datetime.now(timezone.utc) + _TOKEN_TTL).timestamp())
        payload = _b64encode(json.dumps({"sub": user.user_id, "exp": expires, "jti": uuid.uuid4().hex},
                                       separators=(",", ":")).encode())
        signature = _b64encode(hmac.new(self._secret, payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{signature}"

    def token_id(self, token: str) -> str | None:
        claims = self._verified_claims(token)
        return str(claims["jti"]) if claims and claims.get("jti") else None

    def token_expiration(self, token: str) -> datetime | None:
        claims = self._verified_claims(token)
        return datetime.fromtimestamp(int(claims["exp"]), timezone.utc) if claims and claims.get("exp") else None

    def _verified_claims(self, token: str):
        try:
            payload, signature = token.split(".", 1)
            expected = _b64encode(hmac.new(self._secret, payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            return json.loads(_b64decode(payload))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def resolve_token(self, token: str) -> AuthUser | None:
        try:
            payload, signature = token.split(".", 1)
            expected = _b64encode(hmac.new(self._secret, payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            data = json.loads(_b64decode(payload))
            if int(data["exp"]) <= int(datetime.now(timezone.utc).timestamp()):
                return None
            return self._users.get(data["sub"], (None, None, None))[0]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None


def build_auth_provider_from_environment() -> AuthProvider:
    provider = os.getenv("TGOAT_AUTH_PROVIDER", "demo").strip().lower()
    if provider == "demo":
        return DemoAuthProvider()
    if provider == "ldap":
        from backend.app.services.ldap_auth import LDAPAuthProvider
        return LDAPAuthProvider.from_environment()
    raise RuntimeError("TGOAT_AUTH_PROVIDER must be demo or ldap")


auth_provider: AuthProvider = build_auth_provider_from_environment()
bearer = HTTPBearer(auto_error=False)


def get_current_user(request: Request,
                     credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> AuthUser:
    user = auth_provider.resolve_token(credentials.credentials) if credentials else None
    if user is None:
        raise HTTPException(status_code=401, detail="Valid bearer token required",
                            headers={"WWW-Authenticate": "Bearer"})
    database = getattr(request.app.state, "operational_database", None)
    if database is not None and credentials is not None:
        from backend.app.services.postgres_access import PostgreSQLAccessRepository
        repository = getattr(request.app.state, "access_repository", None) or PostgreSQLAccessRepository(database)
        if repository.token_is_revoked(auth_provider.token_id(credentials.credentials)):
            raise HTTPException(status_code=401, detail="Session has been logged out",
                                headers={"WWW-Authenticate": "Bearer"})
        user = effective_user(user, repository)
    request.state.auth_user = user
    from backend.app.services.access_control import required_capabilities
    capabilities = required_capabilities(request.method, request.url.path)
    if capabilities and not user.has_capability(*capabilities):
        raise HTTPException(status_code=403, detail="Capability is not allowed for this operation")
    return user


def effective_user(user: AuthUser, repository) -> AuthUser:
    assignment = repository.get_assignment(user.user_id)
    if assignment is None:
        return user
    roles = tuple(assignment["roles"])
    grants = tuple(assignment["grants"])
    groups = tuple(assignment["groups"])
    roots = None if "central_dispatcher" in roles else tuple(assignment["scope_roots"])
    display_role = next((role for role in ("central_dispatcher", "district_dispatcher", "technician")
                         if role in roles), "manager" if "management.read" in grants else
                         "analyst" if "evidence.read" in grants else "user")
    return replace(user, role=display_role, roles=roles, grants=grants,
                   groups=groups, scope_roots=roots)


def effective_directory_users(request: Request) -> tuple[AuthUser, ...]:
    users = auth_provider.directory_users()
    repository = getattr(request.app.state, "access_repository", None)
    return tuple(effective_user(user, repository) for user in users) if repository else users


def require_role(user: AuthUser, *roles: str) -> None:
    if not user.has_role(*roles):
        raise HTTPException(status_code=403, detail="Role is not allowed for this operation")


def require_capability(user: AuthUser, *capabilities: str) -> None:
    if not user.has_capability(*capabilities):
        raise HTTPException(status_code=403, detail="Capability is not allowed for this operation")


def user_can_access_object(user: AuthUser, object_id: int) -> bool:
    if user.scope_roots is None and user.has_role("central_dispatcher"):
        return True
    if not user.scope_roots:
        return False
    from backend.app.services.reference_data import get_objects_by_id

    objects = get_objects_by_id()
    current = objects.get(object_id)
    visited: set[int] = set()
    while current and current["object_id"] not in visited:
        current_id = current["object_id"]
        if current_id in user.scope_roots:
            return True
        visited.add(current_id)
        parent_id = current.get("parent_id")
        current = objects.get(parent_id) if parent_id is not None else None
    return False


def require_object_scope(user: AuthUser, object_id: int) -> None:
    if not user_can_access_object(user, object_id):
        raise HTTPException(status_code=404, detail="Object not found")


def notification_recipient(user: AuthUser) -> str:
    return user.user_id


def accessible_object_ids(user: AuthUser) -> list[int] | None:
    """Return the exact object scope, or None for an unrestricted user."""
    if user.scope_roots is None and user.has_role("central_dispatcher"):
        return None
    from backend.app.services.reference_data import get_objects_by_id

    return [object_id for object_id in get_objects_by_id()
            if user_can_access_object(user, object_id)]
