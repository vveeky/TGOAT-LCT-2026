from copy import deepcopy
from datetime import datetime, timedelta
from threading import Lock
from typing import Callable, Protocol

from backend.app.config import as_utc, utc_now
from backend.app.schemas import Prediction
from backend.app.services import event_store


class PredictionRepository(Protocol):
    def get(self, prediction_id: int) -> dict | None: ...
    def filter(self, **filters) -> tuple[list[dict], int]: ...
    def set_latest_review(self, prediction_id: int, decision: str) -> bool: ...
    def close(self, prediction_id: int) -> dict | None: ...
    def object_risk_summaries(self, object_ids: list[int]) -> dict[int, dict]: ...
    def channel_risk_summaries(self, channel_ids: list[int]) -> dict[int, dict]: ...
    def risk_distribution(self) -> dict[str, int]: ...
    def active_object_ids(self) -> list[int]: ...


class PredictionReviewRepository(Protocol):
    def list_for_prediction(self, prediction_id: int) -> list[dict]: ...
    def add(self, review: dict) -> dict: ...


class PredictionOutcomeRepository(Protocol):
    def list_for_prediction(self, prediction_id: int) -> list[dict]: ...
    def add(self, outcome: dict) -> dict: ...


class MaintenanceRepository(Protocol):
    def get(self, request_id: int) -> dict | None: ...
    def filter(self, **filters) -> tuple[list[dict], int]: ...
    def create(self, item: dict) -> dict: ...
    def update(self, request_id: int, changes: dict) -> dict | None: ...
    def active_counts_by_object(self, object_ids: list[int]) -> dict[int, int]: ...
    def count_active(self) -> int: ...


class NotificationRepository(Protocol):
    def get(self, notification_id: int) -> dict | None: ...
    def filter_for(self, recipient_key: str, *, object_ids=None, include_global=False,
                   limit: int | None, offset: int) -> tuple[list[dict], int]: ...
    def mark_read(self, notification_id: int, recipient_key: str, read: bool) -> dict | None: ...
    def is_read(self, notification_id: int, recipient_key: str) -> bool | None: ...


class AuditRepository(Protocol):
    def add(self, event: dict) -> dict: ...
    def filter(self, **filters) -> tuple[list[dict], int]: ...


class EventHistoryRepository(Protocol):
    def available(self) -> bool: ...
    def get_channel_history(self, channel_id: int, from_timestamp: datetime | None = None,
                            to_timestamp: datetime | None = None, limit: int = 200,
                            alarm_only: bool = False, offset: int = 0) -> list[dict]: ...
    def get_last_event_times(self, channel_ids: list[int]) -> dict[int, str]: ...


class InMemoryPredictionRepository:
    def __init__(self, initial: list[dict] | Callable[[], list[dict]],
                 objects: dict[int, dict], channels: dict[int, dict],
                 clock=None):
        self._initial_factory = initial if callable(initial) else None
        self._initial = deepcopy([] if callable(initial) else initial)
        self._objects = objects
        self._channels = channels
        self._clock = clock or (lambda: utc_now())
        self.reset()

    def _validate(self, item: dict) -> dict:
        validated = Prediction.model_validate(item).model_dump(mode="json")
        if validated["object_id"] not in self._objects:
            raise ValueError("prediction references unknown object")
        channel_id = validated["channel_id"]
        if channel_id is not None:
            channel = self._channels.get(channel_id)
            if channel is None:
                raise ValueError("prediction references unknown channel")
            if channel["object_id"] != validated["object_id"]:
                raise ValueError("prediction channel does not belong to object")
        return validated

    def reset(self, initial: list[dict] | None = None) -> None:
        if initial is not None:
            self._initial_factory = None
            self._initial = deepcopy(initial)
        elif self._initial_factory is not None:
            self._initial = deepcopy(self._initial_factory())
        self._items = [self._validate(item) for item in self._initial]

    def _expire_stale(self) -> None:
        now = self._clock()
        for item in self._items:
            if item["status"] != "active":
                continue
            created_at = as_utc(datetime.fromisoformat(item["created_at"]))
            if now >= created_at + timedelta(hours=item["forecast_to_hours"]):
                item["status"] = "expired"

    def get(self, prediction_id: int) -> dict | None:
        self._expire_stale()
        item = next((x for x in self._items if x["id"] == prediction_id), None)
        return deepcopy(item) if item else None

    def filter(self, *, object_id=None, object_ids=None, channel_id=None, channel_ids=None, scope_type=None,
               incident_type=None, risk_level=None, status=None, from_timestamp=None,
               to_timestamp=None, limit=100, offset=0) -> tuple[list[dict], int]:
        self._expire_stale()
        items = self._items
        if object_id is not None: items = [x for x in items if x["object_id"] == object_id]
        if object_ids is not None: items = [x for x in items if x["object_id"] in object_ids]
        if channel_id is not None: items = [x for x in items if x["channel_id"] == channel_id]
        if channel_ids is not None: items = [x for x in items if x["channel_id"] in channel_ids]
        if scope_type is not None: items = [x for x in items if x["scope_type"] == scope_type]
        if incident_type is not None: items = [x for x in items if x["incident_type"] == incident_type]
        if risk_level is not None: items = [x for x in items if x["risk_level"] == risk_level]
        if status is not None: items = [x for x in items if x["status"] == status]
        if from_timestamp is not None:
            lower = as_utc(from_timestamp)
            items = [x for x in items if as_utc(datetime.fromisoformat(x["created_at"])) >= lower]
        if to_timestamp is not None:
            upper = as_utc(to_timestamp)
            items = [x for x in items if as_utc(datetime.fromisoformat(x["created_at"])) <= upper]
        items = sorted(items, key=lambda x: x["created_at"], reverse=True)
        return deepcopy(items[offset:] if limit is None else items[offset:offset + limit]), len(items)

    def set_latest_review(self, prediction_id: int, decision: str) -> bool:
        self._expire_stale()
        item = next((x for x in self._items if x["id"] == prediction_id), None)
        if item is None: return False
        item["dispatcher_result"] = decision
        return True

    def close(self, prediction_id: int) -> dict | None:
        self._expire_stale()
        item = next((x for x in self._items if x["id"] == prediction_id), None)
        if item is None:
            return None
        if item["status"] == "active":
            item["status"] = "closed"
        return deepcopy(item)

    def _risk_summaries(self, key: str, ids: list[int]) -> dict[int, dict]:
        self._expire_stale()
        result = {}
        for entity_id in ids:
            items = [x for x in self._items if x["status"] == "active" and x[key] == entity_id]
            ranks = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            highest = max(items, key=lambda x: (
                ranks[x["risk_level"]], as_utc(datetime.fromisoformat(x["created_at"])), x["id"]
            ), default=None)
            result[entity_id] = {
                "risk_score": highest["risk_score"] if highest else None,
                "risk_level": highest["risk_level"] if highest else None,
                "risk_prediction_id": highest["id"] if highest else None,
                "risk_score_kind": highest["score_kind"] if highest else None,
                "active_predictions": len(items),
            }
        return result

    def object_risk_summaries(self, object_ids: list[int]) -> dict[int, dict]:
        return self._risk_summaries("object_id", object_ids)

    def channel_risk_summaries(self, channel_ids: list[int]) -> dict[int, dict]:
        return self._risk_summaries("channel_id", channel_ids)

    def risk_distribution(self) -> dict[str, int]:
        self._expire_stale()
        active = [x for x in self._items if x["status"] == "active"]
        return {level: sum(x["risk_level"] == level for x in active)
                for level in ("low", "medium", "high", "critical")}

    def active_object_ids(self) -> list[int]:
        self._expire_stale()
        return sorted({x["object_id"] for x in self._items if x["status"] == "active"})


class _AppendOnlyRepository:
    def reset(self) -> None:
        self._items: list[dict] = []

    def list_for_prediction(self, prediction_id: int) -> list[dict]:
        return deepcopy([x for x in self._items if x["prediction_id"] == prediction_id])

    def add(self, item: dict) -> dict:
        stored = {"id": max((x["id"] for x in self._items), default=0) + 1, **item}
        self._items.append(stored)
        return deepcopy(stored)


class InMemoryPredictionReviewRepository(_AppendOnlyRepository):
    def __init__(self): self.reset()


class InMemoryPredictionOutcomeRepository(_AppendOnlyRepository):
    def __init__(self): self.reset()


class InMemoryMaintenanceRepository:
    def __init__(self, initial: list[dict] | Callable[[], list[dict]]):
        self._initial_factory = initial if callable(initial) else None
        self._initial = deepcopy([] if callable(initial) else initial)
        self.reset()

    def reset(self, initial: list[dict] | None = None) -> None:
        if initial is not None:
            self._initial_factory = None
            self._initial = deepcopy(initial)
        elif self._initial_factory is not None:
            self._initial = deepcopy(self._initial_factory())
        self._items = deepcopy(self._initial)
    def get(self, request_id: int) -> dict | None:
        item = next((x for x in self._items if x["id"] == request_id), None)
        return deepcopy(item) if item else None
    def filter(self, *, status=None, priority=None, object_id=None, object_ids=None, channel_id=None,
               prediction_id=None, limit=100, offset=0) -> tuple[list[dict], int]:
        items = self._items
        for key, value in (("status", status), ("priority", priority), ("object_id", object_id),
                           ("channel_id", channel_id), ("prediction_id", prediction_id)):
            if value is not None: items = [x for x in items if x[key] == value]
        if object_ids is not None: items = [x for x in items if x["object_id"] in object_ids]
        return deepcopy(items[offset:] if limit is None else items[offset:offset + limit]), len(items)
    def create(self, item: dict) -> dict:
        stored = {"id": max((x["id"] for x in self._items), default=0) + 1, **item}
        self._items.append(stored)
        return deepcopy(stored)
    def update(self, request_id: int, changes: dict) -> dict | None:
        item = next((x for x in self._items if x["id"] == request_id), None)
        if item is None: return None
        item.update(changes)
        return deepcopy(item)

    def active_counts_by_object(self, object_ids: list[int]) -> dict[int, int]:
        return {object_id: sum(
            x["object_id"] == object_id and x["status"] not in {"closed", "cancelled"}
            for x in self._items
        ) for object_id in object_ids}

    def count_active(self) -> int:
        return sum(x["status"] not in {"closed", "cancelled"} for x in self._items)


class InMemoryNotificationRepository:
    def __init__(self, initial: list[dict] | Callable[[], list[dict]]):
        self._initial_factory = initial if callable(initial) else None
        self._initial = deepcopy([] if callable(initial) else initial)
        self.reset()
    def reset(self, initial: list[dict] | None = None) -> None:
        if initial is not None:
            self._initial_factory = None
            self._initial = deepcopy(initial)
        elif self._initial_factory is not None:
            self._initial = deepcopy(self._initial_factory())
        self._items = deepcopy(self._initial)
        self._receipts = {}
    def get(self, notification_id: int) -> dict | None:
        item = next((x for x in self._items if x["id"] == notification_id), None)
        return deepcopy(item) if item is not None else None
    def filter_for(self, recipient_key: str, *, object_ids=None, include_global=False,
                   limit=100, offset=0) -> tuple[list[dict], int]:
        items = [self._with_read(x, recipient_key) for x in self._items]
        if object_ids is not None:
            items = [x for x in items if x.get("object_id") in object_ids
                     or (include_global and x.get("object_id") is None)]
        page = items[offset:] if limit is None else items[offset:offset + limit]
        return page, sum(not x["read"] for x in items)
    def mark_read(self, notification_id: int, recipient_key: str, read: bool) -> dict | None:
        item = next((x for x in self._items if x["id"] == notification_id), None)
        if item is None: return None
        key = (notification_id, recipient_key)
        if read: self._receipts[key] = utc_now().isoformat()
        else: self._receipts.pop(key, None)
        return self._with_read(item, recipient_key)
    def is_read(self, notification_id: int, recipient_key: str) -> bool | None:
        item = next((x for x in self._items if x["id"] == notification_id), None)
        return None if item is None else (notification_id, recipient_key) in self._receipts
    def _with_read(self, item: dict, recipient_key: str) -> dict:
        return {**deepcopy(item), "read": (item["id"], recipient_key) in self._receipts}


class InMemoryAuditRepository:
    """Append-only request/domain audit store; public API exposes no mutation operation."""

    def __init__(self):
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._items: list[dict] = []

    def add(self, event: dict) -> dict:
        with self._lock:
            stored = {"id": len(self._items) + 1, **deepcopy(event)}
            self._items.append(stored)
            return deepcopy(stored)

    def filter(self, *, user_id=None, viewer_user_id=None, role=None, action=None, entity_type=None,
               entity_id=None, object_id=None, object_ids=None, include_global=False,
               start=None, end=None, limit=100, offset=0) -> tuple[list[dict], int]:
        with self._lock:
            items = list(self._items)
            for key, value in (("user_id", user_id), ("role", role), ("action", action),
                               ("entity_type", entity_type), ("entity_id", entity_id),
                               ("object_id", object_id)):
                if value is not None:
                    if key == "action":
                        items = [item for item in items
                                 if item.get(key) == value
                                 or (isinstance(item.get(key), str)
                                     and item[key].startswith(f"{value} "))]
                    else:
                        items = [item for item in items if item.get(key) == value]
            if object_ids is not None:
                items = [item for item in items if item.get("object_id") in object_ids
                         or item.get("user_id") == viewer_user_id
                         or (include_global and item.get("object_id") is None)]
            if start is not None:
                items = [item for item in items if datetime.fromisoformat(item["timestamp"]) >= start]
            if end is not None:
                items = [item for item in items if datetime.fromisoformat(item["timestamp"]) < end]
            items.sort(key=lambda item: (item["timestamp"], item["id"]), reverse=True)
            page = items[offset:] if limit is None else items[offset:offset + limit]
            return deepcopy(page), len(items)


class DuckDBEventHistoryRepository:
    def available(self) -> bool: return event_store.history_storage_available()
    def get_channel_history(self, channel_id, from_timestamp=None, to_timestamp=None, limit=200, alarm_only=False, offset=0):
        return event_store.get_channel_history(channel_id, from_timestamp, to_timestamp, limit, alarm_only, offset)
    def get_last_event_times(self, channel_ids):
        return event_store.get_last_event_times(channel_ids)
