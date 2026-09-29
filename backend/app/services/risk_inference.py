"""Backend service wrapper for the frozen risk model.

This service deliberately has no mock/default score. Provider, reference,
artifact, schema and inference failures all become explicit degraded results.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable

from ml.inference.load_risk_model import FrozenRiskModel, FrozenRiskModelError, load_frozen_model

from backend.app.services.ml_features import FeatureProviderError, FeatureSnapshotStore
from backend.app.config import utc_now


class RiskInferenceService:
    def __init__(self, provider: FeatureSnapshotStore, *, loader: Callable[[], FrozenRiskModel] = load_frozen_model,
                 clock: Callable[[], datetime] = utc_now):
        self.provider = provider
        self._loader = loader
        self._clock = clock
        self._model: FrozenRiskModel | None = None
        self._load_error: Exception | None = None
        self._load_count = 0
        self._load_once()

    def _load_once(self) -> None:
        if self._model is not None or self._load_error is not None:
            return
        self._load_count += 1
        try:
            self._model = self._loader()
        except Exception as error:  # artifact errors are part of degraded API behavior
            self._load_error = error

    @property
    def load_count(self) -> int:
        return self._load_count

    def predict(self, object_id: int, snapshot_ts: datetime) -> dict[str, object]:
        snapshot_iso = snapshot_ts.isoformat()
        if self._load_error is not None:
            return self._degraded(object_id, snapshot_iso, "artifact_integrity_error", self._load_error)
        if self._model is None:
            return self._degraded(object_id, snapshot_iso, "model_unavailable", None)
        try:
            features = self.provider.snapshot(object_id, snapshot_ts)
            result = self._model.predict(features, object_id=object_id, snapshot_ts=snapshot_iso)
            metadata = self.provider.metadata() if hasattr(self.provider, "metadata") else {}
            return {
                **result,
                "status": "available",
                "risk_score_kind": "ml",
                "degraded_reason": None,
                "generated_at": self._clock().isoformat(),
                "reference_snapshot_id": metadata.get("reference_snapshot_id"),
                "reference_hash": metadata.get("reference_hash"),
                "source_freshness_ts": metadata.get("available_to"),
            }
        except FeatureProviderError as error:
            message = str(error).lower()
            reason = ("reference_mismatch" if "reference" in message
                      else "feature_schema_error" if "schema" in message
                      else "provider_unavailable")
            return self._degraded(object_id, snapshot_iso, reason, error)
        except FrozenRiskModelError as error:
            return self._degraded(object_id, snapshot_iso, "feature_schema_error", error)
        except Exception as error:
            return self._degraded(object_id, snapshot_iso, "inference_error", error)

    def batch_predict(self, requests: Iterable[tuple[int, datetime]]) -> list[dict[str, object]]:
        return [self.predict(object_id, snapshot_ts) for object_id, snapshot_ts in requests]

    def _degraded(self, object_id: int, snapshot_ts: str, reason: str,
                  error: Exception | None) -> dict[str, object]:
        return {
            "object_id": object_id,
            "snapshot_ts": snapshot_ts,
            "risk_probability": None,
            "risk_flag": None,
            "threshold": None,
            "model_version": None,
            "feature_schema_version": None,
            "status": "degraded",
            "risk_score_kind": "ml",
            "degraded_reason": reason,
            "error": str(error) if error else None,
            "generated_at": self._clock().isoformat(),
            "reference_snapshot_id": None,
            "reference_hash": None,
            "source_freshness_ts": None,
        }
