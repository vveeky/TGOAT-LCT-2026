import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.app.config import CORS_ALLOWED_ORIGINS
from backend.app.services.reference_data import ReferenceDataUnavailableError

from backend.app.api.routes import (
    health,
    dashboard,
    objects,
    channels,
    predictions,
    maintenance,
    notifications,
    auth,
    audit,
    access,
    operational,
    replay,
    signals,
    scenarios,
    integrations,
)
from backend.app.services.auth import auth_provider, get_current_user
from backend.app.services.audit import bind_audit_database, record_request_event, resolve_request_entity
from backend.app.services.postgres_access import PostgreSQLAccessRepository
from backend.app.services.postgres_audit import PostgreSQLAuditRepository
from backend.app.services.runtime_ml import build_operational_cycle_service, build_postgres_operational_runtime
from backend.app.services.operational_read_model import OperationalRiskReadService
from backend.app.services.reference_data import get_objects_data
from backend.app.services.operational_actions import SQLiteOperationalActionRepository
from backend.app.services.replay_runtime import replay_runtime_from_environment
from backend.app.services.dispatcher_workflow import SQLiteDispatcherWorkflow

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    mode = os.environ.get("TGOAT_RUNTIME_MODE", "normal").strip().lower()
    if mode not in {"normal", "replay"}:
        raise RuntimeError("TGOAT_RUNTIME_MODE must be normal or replay")
    application.state.runtime_mode = mode
    if mode == "replay":
        application.state.operational_database = None
        application.state.audit_repository = None
        application.state.access_repository = None
        runtime = replay_runtime_from_environment()

        def bind_replay_context(context):
            application.state.risk_inference_service = context["cycle"].inference_service
            application.state.operational_forecast_service = context["forecast"]
            application.state.operational_cycle_service = context["cycle"]
            application.state.operational_cycle_repository = context["cycle_repository"]
            application.state.operational_action_repository = context["actions"]
            application.state.operational_notification_repository = context["notifications"]
            application.state.operational_read_service = context["read_model"]
            application.state.dispatcher_workflow = context["workflow"]

        runtime.bind_context(bind_replay_context)
        application.state.replay_runtime = runtime
        runtime.start_worker()
        if runtime.auto_start:
            runtime.start()
        try:
            yield
        finally:
            runtime.close()
        return

    storage_backend = os.environ.get("TGOAT_OPERATIONAL_STORAGE", "postgres").strip().lower()
    if storage_backend == "postgres":
        runtime = build_postgres_operational_runtime()
        cycle = runtime["cycle"]
        application.state.operational_storage = "postgres"
        application.state.operational_database = runtime["database"]
        application.state.audit_repository = PostgreSQLAuditRepository(runtime["database"])
        application.state.access_repository = PostgreSQLAccessRepository(runtime["database"])
        application.state.risk_inference_service = cycle.inference_service
        application.state.operational_forecast_service = cycle.forecast_service
        application.state.dispatcher_workflow = runtime["workflow"]
        application.state.operational_cycle_service = cycle
        application.state.operational_cycle_repository = cycle.cycle_repository
        application.state.operational_action_repository = runtime["actions"]
        application.state.operational_notification_repository = runtime["notifications"]
        application.state.operational_read_service = runtime["read_model"]
        live_service = None
        if os.environ.get("TGOAT_SIMULATED_LIVE_MODE", "0").strip().lower() in {"1", "true", "yes"}:
            from backend.app.services.simulated_live import SimulatedLiveOperationalService
            live_service = SimulatedLiveOperationalService(
                runtime["database"], runtime,
                manifest_path=os.environ.get("TGOAT_SIMULATED_LIVE_MANIFEST") or None,
                interval_seconds=float(os.environ.get("TGOAT_SIMULATED_LIVE_INTERVAL_SECONDS", "300")),
            )
            application.state.simulated_live_service = live_service
            live_service.start_worker(running=os.environ.get(
                "TGOAT_SIMULATED_LIVE_AUTO_START", "0").strip().lower() in {"1", "true", "yes"})
        try:
            yield
        finally:
            if live_service is not None:
                live_service.close()
            runtime["database"].close()
        return
    if storage_backend != "sqlite-test":
        raise RuntimeError("TGOAT_OPERATIONAL_STORAGE must be postgres or explicit sqlite-test")

    # Explicit isolated test mode only; production/demo normal mode is PostgreSQL.
    application.state.operational_storage = "sqlite-test"
    application.state.operational_database = None
    application.state.audit_repository = None
    application.state.access_repository = None
    cycle = build_operational_cycle_service()
    application.state.risk_inference_service = cycle.inference_service
    application.state.operational_forecast_service = cycle.forecast_service
    application.state.dispatcher_workflow = SQLiteDispatcherWorkflow(cycle.forecast_service.repository)
    application.state.operational_cycle_service = cycle
    application.state.operational_action_repository = SQLiteOperationalActionRepository(
        cycle.forecast_service.repository.path
    )
    application.state.operational_notification_repository = cycle.forecast_service.notification_repository
    application.state.operational_read_service = OperationalRiskReadService(
        cycle.forecast_service, cycle.cycle_repository,
        lambda: [item["object_id"] for item in get_objects_data() if item["has_channels"]],
    )
    try:
        yield
    finally:
        forecast = cycle.forecast_service
        resources = (
            getattr(forecast, "repository", None),
            getattr(getattr(forecast, "recommendation_service", None), "repository", None),
            getattr(forecast, "notification_repository", None),
            getattr(cycle, "cycle_repository", None),
            getattr(application.state, "operational_action_repository", None),
        )
        closed: set[int] = set()
        for resource in resources:
            close = getattr(resource, "close", None)
            if callable(close) and id(resource) not in closed:
                closed.add(id(resource))
                try:
                    close()
                except Exception:
                    logger.exception("Failed to close operational SQLite resource")


app = FastAPI(
    title="T.G.O.A.T. Moscollector API",
    version="0.2.0",
    description="Backend API for incident risk forecasting and maintenance planning.",
    lifespan=lifespan,
)


@app.middleware("http")
async def audit_business_requests(request: Request, call_next):
    path = request.url.path
    normalized_path = path.rstrip("/") or "/"
    technical_paths = {"/api/v1/health", "/docs", "/redoc", "/openapi.json",
                       "/docs/oauth2-redirect"}
    is_business_request = path.startswith("/api/v1/") and normalized_path not in technical_paths
    if not is_business_request:
        return await call_next(request)

    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    token_user = auth_provider.resolve_token(token) if scheme.lower() == "bearer" and token else None
    try:
        entity_type, entity_id, object_id = resolve_request_entity(path, request.query_params)
    except Exception:
        logger.exception("Audit entity resolution failed")
        entity_type, entity_id = "api_request", None
        object_id = None
    method = request.method
    action = f"request.{method.lower()}"

    def save(result: str, status_code: int) -> None:
        for event_action in (
            action,
            "access.denied" if status_code == 403 else
            "access.privileged_read" if method == "GET" and status_code < 400 and
            normalized_path.startswith(("/api/v1/audit-events", "/api/v1/access/")) else None,
        ):
            if event_action is not None:
                record_request_event(
                    user=getattr(request.state, "auth_user", token_user), method=method,
                    path=path, action=event_action, entity_type=entity_type,
                    entity_id=entity_id, object_id=object_id, result=result,
                    status_code=status_code, request_id=request_id,
                )

    database = getattr(request.app.state, "operational_database", None)
    is_atomic_write = database is not None and method in {"POST", "PUT", "PATCH", "DELETE"} and \
        normalized_path != "/api/v1/auth/login"

    class RollbackResponse(Exception):
        def __init__(self, response):
            self.response = response

    def outcome(status_code):
        return ("success" if status_code < 400 else
                "unauthorized" if status_code == 401 else
                "forbidden" if status_code == 403 else
                "not_found" if status_code == 404 else
                "validation_error" if status_code == 422 else "error")

    with bind_audit_database(database):
        try:
            if is_atomic_write:
                with database.unit_of_work():
                    response = await call_next(request)
                    if response.status_code >= 400:
                        raise RollbackResponse(response)
                    save("success", response.status_code)
            else:
                response = await call_next(request)
                save(outcome(response.status_code), response.status_code)
        except RollbackResponse as error:
            response = error.response
            save(outcome(response.status_code), response.status_code)
        except Exception:
            logger.exception("Business API request failed before response")
            save("error", 500)
            raise
    response.headers["X-Request-ID"] = request_id
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(
    health.router,
    prefix="/api/v1",
)


@app.exception_handler(ReferenceDataUnavailableError)
def reference_data_unavailable(_: Request, error: ReferenceDataUnavailableError):
    return JSONResponse(status_code=503, content={
        "detail": {"code": "reference_data_unavailable", "message": str(error)}
    })

app.include_router(
    auth.router,
    prefix="/api/v1",
)

app.include_router(
    audit.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(access.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])

app.include_router(
    dashboard.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    objects.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    channels.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    predictions.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    maintenance.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    notifications.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    operational.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    replay.router,
    prefix="/api/v1",
)
app.include_router(signals.router, prefix="/api/v1")
app.include_router(scenarios.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
app.include_router(integrations.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])


@app.get("/")
def root():
    return {
        "service": "T.G.O.A.T. Moscollector API",
        "version": "0.2.0",
        "docs": "/docs",
    }
