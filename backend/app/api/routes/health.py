from fastapi import APIRouter, Request

from backend.app.services.reference_data import reference_data_health

router = APIRouter(tags=["Health"])


@router.get("/health")
def health(request: Request):
    # Health must stay usable even when heavy annual history is intentionally
    # omitted from the hackathon deployment bundle.
    components = {"reference_data": reference_data_health()}
    try:
        from backend.app.services.event_store import get_history_storage_health
        components["event_history"] = get_history_storage_health()
    except Exception as error:
        components["event_history"] = {"status": "unavailable", "detail": str(error)[:300]}
    database = getattr(request.app.state, "operational_database", None)
    if database is not None:
        try:
            components["postgresql"] = {"status": "available", **database.healthcheck()}
        except Exception as error:
            components["postgresql"] = {"status": "unavailable", "detail": str(error)[:300]}
    else:
        components["postgresql"] = {"status": "not_applicable" if getattr(
            request.app.state, "runtime_mode", "normal") == "replay" else "unavailable"}
    live = getattr(request.app.state, "simulated_live_service", None)
    if live is not None:
        try:
            state = live.status()
            components["simulated_live"] = {"status": "available", **state}
        except Exception as error:
            components["simulated_live"] = {"status": "unavailable", "detail": str(error)[:300]}
    required = [value for key, value in components.items() if key in {"reference_data", "postgresql"}]
    ok = all(value.get("status") in {"available", "not_applicable"} for value in required)
    return {"status": "ok" if ok else "degraded", "service": "tgoat-api", "components": components}
