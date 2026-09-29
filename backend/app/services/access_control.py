"""Server-side capability gates for API routes."""

from __future__ import annotations


def required_capabilities(method: str, path: str) -> tuple[str, ...]:
    verb = method.upper()
    path = path.rstrip("/") or "/"
    if path in {"/api/v1/auth/me", "/api/v1/auth/logout"}:
        return ()
    if path == "/api/v1/audit-events":
        return ("audit.read",)
    if path.startswith("/api/v1/access/"):
        return ("access.manage",)
    if path.startswith("/api/v1/scenarios/"):
        return ("evidence.read",)
    if path.startswith("/api/v1/signals"):
        return ("signals.decide",) if verb == "POST" and path.endswith("/decision") else ("signals.read",)
    if path.startswith("/api/v1/replay/"):
        return ("replay.read",) if verb == "GET" and path.endswith("/status") else ("replay.control",)
    if path.startswith("/api/v1/maintenance-requests"):
        if path.endswith("/eligible-technicians"):
            return ("requests.assign",)
        if verb == "GET":
            return ("requests.read", "management.read")
        if path == "/api/v1/maintenance-requests" and verb == "POST":
            return ("requests.create",)
        if verb == "PATCH":
            return ("requests.edit",)
        if verb == "POST":
            action = path.rsplit("/", 1)[-1]
            if action in {"start", "result"}:
                return ("tasks.execute",)
            if action in {"submit", "cancel"}:
                return ("requests.create", "requests.dispatch")
            return ("requests.dispatch",)
    if path.startswith("/api/v1/notifications"):
        return ("notifications.read",) if verb == "GET" else ("notifications.update",)
    if path.startswith("/api/v1/predictions"):
        if verb == "GET":
            return ("evidence.read",)
        if path.endswith("/review"):
            return ("prediction.review",)
        if path.endswith("/outcomes"):
            return ("prediction.outcome",)
        return ("prediction.close",)
    if path.startswith("/api/v1/operational"):
        if path == "/api/v1/operational/dashboard":
            return ("objects.read", "management.read")
        if verb == "GET":
            return ("evidence.read",)
        if path.endswith("/review"):
            return ("prediction.review",)
        if path.endswith("/outcomes"):
            return ("prediction.outcome",)
    if path.startswith("/api/v1/objects"):
        if verb == "POST" and path.endswith("/risk/operational"):
            return ("prediction.run",)
        if path.endswith("/planned-work"):
            return ("management.read",)
        if verb == "GET" and any(path.endswith(suffix) for suffix in
                                  ("/risk", "/risk/history", "/risk/cards", "/risk/recommendations", "/channels")):
            return ("evidence.read",)
        return ("objects.read", "management.read", "evidence.read")
    if path.startswith("/api/v1/channels"):
        return ("evidence.read",)
    if path == "/api/v1/dashboard":
        return ("management.read",)
    return ()
