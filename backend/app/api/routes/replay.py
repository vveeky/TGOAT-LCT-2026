"""Authenticated control surface for the explicitly enabled demo replay."""

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.services.auth import AuthUser, get_current_user
from backend.app.services.replay_runtime import ReplayTransitionError


router = APIRouter(prefix="/replay", tags=["Historical replay"])
DISPATCHER_ROLES = {"district_dispatcher", "central_dispatcher"}


def _runtime(request: Request):
    runtime = getattr(request.app.state, "replay_runtime", None)
    if runtime is None:
        return None
    return runtime


def _enabled(request: Request):
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(409, detail={"code": "replay_disabled", "message": "Replay controls are available only in replay mode"})
    return runtime


def _dispatcher(user: AuthUser):
    if not user.has_role(*DISPATCHER_ROLES):
        raise HTTPException(403, detail="Dispatcher role is required for replay controls")


@router.get("/status")
def replay_status(request: Request, user: AuthUser = Depends(get_current_user)):
    runtime = _runtime(request)
    if runtime is None:
        return {"mode": "normal", "state": "disabled"}
    return runtime.status()


@router.post("/start")
def replay_start(request: Request, user: AuthUser = Depends(get_current_user)):
    _dispatcher(user)
    return _enabled(request).start()


@router.post("/pause")
def replay_pause(request: Request, user: AuthUser = Depends(get_current_user)):
    _dispatcher(user)
    return _enabled(request).pause()


@router.post("/resume")
def replay_resume(request: Request, user: AuthUser = Depends(get_current_user)):
    _dispatcher(user)
    return _enabled(request).resume()


@router.post("/step")
def replay_step(request: Request, user: AuthUser = Depends(get_current_user)):
    _dispatcher(user)
    try:
        return _enabled(request).step()
    except ReplayTransitionError as error:
        raise HTTPException(409, detail={"code": "invalid_replay_transition", "message": str(error)}) from error


@router.post("/reset")
def replay_reset(request: Request, user: AuthUser = Depends(get_current_user)):
    _dispatcher(user)
    return _enabled(request).reset()


@router.patch("/speed")
def replay_speed(body: dict, request: Request, user: AuthUser = Depends(get_current_user)):
    _dispatcher(user)
    speed = body.get("speed")
    if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 3600:
        raise HTTPException(422, detail="speed must be an integer from 1 through 3600")
    return _enabled(request).set_speed(speed)
