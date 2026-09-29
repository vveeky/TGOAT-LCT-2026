from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.schemas import AccessAssignmentUpdate, AccessPrincipalResponse
from backend.app.services.auth import AuthUser, auth_provider, get_current_user, require_capability
from backend.app.services.postgres_access import (
    AccessAssignmentConflict,
    InvalidAccessAssignment,
    PostgreSQLAccessRepository,
)


router = APIRouter(prefix="/access/principals", tags=["Access administration"])


def _repository(request: Request):
    repository = getattr(request.app.state, "access_repository", None)
    if repository is None:
        raise HTTPException(503, detail="Persistent access administration is unavailable")
    return repository


def _present(user, assignment=None):
    if assignment is None:
        roles, grants, groups = user.roles, user.grants, user.groups
        roots, version = user.scope_roots, 0
    else:
        roles, grants, groups = assignment["roles"], assignment["grants"], assignment["groups"]
        roots, version = assignment["scope_roots"], assignment["version"]
    display = next((role for role in ("central_dispatcher", "district_dispatcher", "technician",
                                      "system_administrator") if role in roles),
                   "manager" if "management.read" in grants else
                   "analyst" if "evidence.read" in grants else user.role)
    return {"user_id": user.user_id, "username": user.username, "role": display,
            "roles": list(roles), "grants": list(grants), "groups": list(groups),
            "scope_roots": list(roots) if roots is not None else None, "version": version}


@router.get("", response_model=list[AccessPrincipalResponse])
def list_principals(request: Request, actor: AuthUser = Depends(get_current_user)):
    require_capability(actor, "access.manage")
    repository = _repository(request)
    return [_present(user, repository.get_assignment(user.user_id))
            for user in auth_provider.directory_users()]


@router.put("/{user_id}", response_model=AccessPrincipalResponse)
def update_principal(user_id: str, body: AccessAssignmentUpdate, request: Request,
                     actor: AuthUser = Depends(get_current_user)):
    require_capability(actor, "access.manage")
    target = next((user for user in auth_provider.directory_users() if user.user_id == user_id), None)
    if target is None:
        raise HTTPException(404, detail="Directory user not found")
    try:
        assignment = _repository(request).save_assignment(
            target=user_id, username=target.username, roles=body.roles, grants=body.grants,
            groups=body.groups, expected_version=body.expected_version, actor=actor,
        )
    except AccessAssignmentConflict as error:
        raise HTTPException(409, detail=str(error)) from error
    except InvalidAccessAssignment as error:
        raise HTTPException(422, detail=str(error)) from error
    return _present(target, assignment)
