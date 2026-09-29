from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.auth import DemoAuthProvider, auth_provider


def _token(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_valid_and_invalid_login_and_user_identity():
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "tech-demo"})
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    identity = client.get("/api/v1/auth/me")
    assert identity.status_code == 200
    assert identity.json()["user_id"] == "tech-28"
    assert identity.json()["role"] == "technician"
    assert client.post("/api/v1/auth/login", json={"username": "tech-28", "password": "wrong"}).status_code == 401


def test_login_expiry_matches_ldap_short_session(monkeypatch):
    from backend.app.api.routes import auth as auth_route
    from backend.app.services.auth import AuthUser
    from backend.app.services.ldap_auth import LDAPAuthProvider

    provider = LDAPAuthProvider(url="ldaps://localhost", base_dn="dc=tgoat,dc=local",
                                token_secret="test-ldap-secret-that-is-long-enough-123")
    user = AuthUser("central", "central", "central_dispatcher", None)
    monkeypatch.setattr(provider, "authenticate", lambda _username, _password: user)
    monkeypatch.setattr(auth_route, "auth_provider", provider)

    response = TestClient(app).post("/api/v1/auth/login",
                                    json={"username": "central", "password": "secret"})

    assert response.status_code == 200
    assert 890 <= response.json()["expires_in"] <= 900


def test_ldap_transport_accepts_none_from_connection_open(monkeypatch):
    from backend.app.services.ldap_auth import LDAPAuthProvider

    provider = LDAPAuthProvider(url="ldaps://localhost", base_dn="dc=tgoat,dc=local",
                                token_secret="test-ldap-secret-that-is-long-enough-123")

    class Connection:
        closed = False

        def open(self):
            return None

        def bind(self):
            return True

        def unbind(self):
            return True

    class LDAP3:
        Connection = staticmethod(lambda *_args, **_kwargs: Connection())

    connection = Connection()
    monkeypatch.setattr(provider, "_ldap3", lambda: LDAP3)
    monkeypatch.setattr(provider, "_server", lambda: object())
    monkeypatch.setattr(LDAP3, "Connection", staticmethod(lambda *_args, **_kwargs: connection))

    assert provider._secure_bind("cn=service", "secret") is connection


def test_ldap_directory_outage_fails_login_closed_with_401(monkeypatch):
    from backend.app.api.routes import auth as auth_route
    from backend.app.services.ldap_auth import LDAPAuthProvider, LDAPConfigurationError

    provider = LDAPAuthProvider(url="ldaps://localhost", base_dn="dc=tgoat,dc=local",
                                token_secret="test-ldap-secret-that-is-long-enough-123")
    monkeypatch.setattr(provider, "_lookup", lambda _username: (_ for _ in ()).throw(
        LDAPConfigurationError("directory unavailable")))
    monkeypatch.setattr(auth_route, "auth_provider", provider)

    response = TestClient(app).post("/api/v1/auth/login",
                                    json={"username": "central", "password": "secret"})

    assert response.status_code == 401


def test_business_api_requires_a_valid_bearer_token():
    client = TestClient(app)
    assert client.get("/api/v1/objects").status_code == 401
    client.headers["Authorization"] = "Bearer malformed"
    assert client.get("/api/v1/objects").status_code == 401


def test_scope_and_role_enforcement():
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {_token(client, 'tech-28', 'tech-demo')}"
    assert client.get("/api/v1/objects/28").status_code == 200
    assert client.get("/api/v1/objects/9").status_code == 404
    scoped_predictions = client.get("/api/v1/predictions", params={"object_id": 9}).json()
    assert scoped_predictions["items"] == [] and scoped_predictions["total"] == 0
    assert client.post("/api/v1/predictions/1042/close").status_code == 403
    assert client.patch("/api/v1/maintenance-requests/501", json={"status": "closed"}).status_code == 403

    client.headers["Authorization"] = f"Bearer {_token(client, 'dispatch-9', 'dispatch-demo')}"
    assert client.get("/api/v1/objects/28").status_code == 200
    assert client.post("/api/v1/predictions/1042/close").status_code == 200


def test_demo_users_are_independent_and_provider_accepts_new_accounts():
    provider = DemoAuthProvider([
        {"username": "new-tech", "password": "safe-demo", "role": "technician", "scope_roots": [28]},
    ], token_secret="test-secret")
    user = provider.authenticate("new-tech", "safe-demo")
    assert user is not None and user.user_id == "new-tech"
    token = provider.issue_token(user)
    assert provider.resolve_token(token) == user
    assert provider.authenticate("new-tech", "wrong") is None
    assert auth_provider.authenticate("tech-28", "tech-demo") != auth_provider.authenticate("tech-9", "tech-demo-2")
