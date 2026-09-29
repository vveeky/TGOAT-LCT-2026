"""Integrity-checked loader and strict ML-side inference contract.

The FastAPI runtime loads this trusted local artifact through the validated
runtime factory. User-supplied pickle/joblib files are never accepted.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT = ROOT / "ml/artifacts/risk_baseline_v1/phase2_hgb.joblib"
DEFAULT_MANIFEST = ROOT / "ml/artifacts/risk_baseline_v1/phase2_manifest.json"


class FrozenRiskModelError(ValueError):
    """A deterministic contract or artifact-integrity failure."""


@dataclass(frozen=True)
class FrozenRiskModel:
    model: object
    features: tuple[str, ...]
    threshold: float
    model_version: str
    feature_schema_version: str

    def predict(self, values: Mapping[str, object], *, object_id: int | None = None, snapshot_ts: str | None = None) -> dict:
        expected = set(self.features)
        actual = set(values)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            raise FrozenRiskModelError(f"feature schema mismatch: missing={missing}, extra={extra}")
        ordered: list[float] = []
        for name in self.features:
            value = values[name]
            if isinstance(value, bool):
                value = float(value)
            if value is None:
                ordered.append(float("nan"))
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise FrozenRiskModelError(f"feature {name!r} is not numeric") from exc
            if not math.isfinite(numeric):
                raise FrozenRiskModelError(f"feature {name!r} is not finite")
            ordered.append(numeric)
        model_input = pd.DataFrame([ordered], columns=self.features, dtype=np.float64)
        probability = float(self.model.predict_proba(model_input)[0, 1])
        return {"risk_probability": probability, "risk_flag": probability >= self.threshold, "threshold": self.threshold, "model_version": self.model_version, "feature_schema_version": self.feature_schema_version, "object_id": object_id, "snapshot_ts": snapshot_ts}


def load_frozen_model(artifact_path: Path = DEFAULT_ARTIFACT, manifest_path: Path = DEFAULT_MANIFEST) -> FrozenRiskModel:
    if not artifact_path.is_file():
        raise FrozenRiskModelError(f"artifact missing: {artifact_path}")
    if not manifest_path.is_file():
        raise FrozenRiskModelError(f"manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    actual_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if actual_sha != manifest.get("sha256"):
        raise FrozenRiskModelError("artifact SHA256 mismatch")
    features = tuple(manifest.get("features", ()))
    if len(features) != manifest.get("feature_count") or len(set(features)) != len(features):
        raise FrozenRiskModelError("manifest feature_count/order is invalid")
    try:
        payload = joblib.load(artifact_path)
    except Exception as exc:
        raise FrozenRiskModelError("trusted artifact could not be loaded") from exc
    if tuple(payload.get("features", ())) != features:
        raise FrozenRiskModelError("artifact feature order differs from manifest")
    if payload.get("dataset_version") != manifest.get("dataset_version"):
        raise FrozenRiskModelError("artifact dataset version differs from manifest")
    if float(payload.get("threshold")) != float(manifest.get("threshold")):
        raise FrozenRiskModelError("artifact threshold differs from manifest")
    return FrozenRiskModel(payload["model"], features, float(manifest["threshold"]), "risk_baseline_v1_phase1_1_hgb", manifest["dataset_version"])
