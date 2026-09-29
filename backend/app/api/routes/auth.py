import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from backend.app.schemas import AuthTokenResponse, CurrentUserResponse, LoginRequest
from backend.app.services.auth import auth_provider, bearer, effective_user, get_current_user
from backend.app.services.audit import record_audit_event, record_login_event

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=AuthTokenResponse)
def login(body: LoginRequest, request: Request):
    user = auth_provider.authenticate(body.username, body.password)
    if user is None:
        record_login_event(username=body.username, user=None, success=False,
                           request_id=getattr(request.state, "request_id", None))
        raise HTTPException(status_code=401, detail="Invalid username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    repository = getattr(request.app.state, "access_repository", None)
    if repository is not None:
        user = effective_user(user, repository)
    record_login_event(username=body.username, user=user, success=True,
                       request_id=getattr(request.state, "request_id", None))
    token = auth_provider.issue_token(user)
    expires_at = auth_provider.token_expiration(token)
    expires_in = (max(1, math.ceil((expires_at - datetime.now(timezone.utc)).total_seconds()))
                  if expires_at is not None else 28800)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


@router.get("/me", response_model=CurrentUserResponse)
def me(user=Depends(get_current_user)):
    return {"user_id": user.user_id, "username": user.username, "role": user.role,
            "roles": list(user.roles), "grants": list(user.grants), "groups": list(user.groups),
            "scope_roots": list(user.scope_roots) if user.scope_roots is not None else None}


@router.post("/logout", status_code=204)
def logout(request: Request, user=Depends(get_current_user),
           credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    repository = getattr(request.app.state, "access_repository", None)
    if repository is not None:
        token_id = auth_provider.token_id(credentials.credentials)
        expires_at = auth_provider.token_expiration(credentials.credentials)
        repository.revoke_token(token_id, user.user_id, expires_at)
    record_audit_event(user, "logout", "session", user.user_id)
