import json
from pathlib import Path

import numpy as np
import pytest

from ml.inference.load_risk_model import FrozenRiskModelError, load_frozen_model


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/risk_baseline_v1_golden.json"


def test_golden_fixture_predictions_and_feature_order():
    payload = json.loads(FIXTURE.read_text())
    model = load_frozen_model()
    assert list(model.features) == payload["feature_names"]
    for item in payload["items"]:
        values = dict(zip(model.features, item["features"]))
        result = model.predict(values, object_id=item["object_id"], snapshot_ts=item["snapshot_ts"])
        assert result["model_version"] == item["model_version"]
        assert result["risk_flag"] is item["expected_risk_flag"]
        assert result["risk_probability"] == pytest.approx(item["expected_probability"], abs=1e-12)


def test_contract_rejects_missing_extra_wrong_type_and_nan():
    model = load_frozen_model()
    values = {name: 0.0 for name in model.features}
    values[model.features[0]] = None
    assert np.isfinite(model.predict(values)["risk_probability"])
    missing = dict(values); missing.pop(model.features[0])
    with pytest.raises(FrozenRiskModelError, match="schema mismatch"):
        model.predict(missing)
    extra = dict(values); extra["unexpected"] = 1
    with pytest.raises(FrozenRiskModelError, match="schema mismatch"):
        model.predict(extra)
    wrong_type = dict(values); wrong_type[model.features[0]] = "not-a-number"
    with pytest.raises(FrozenRiskModelError, match="not numeric"):
        model.predict(wrong_type)
    nan_value = dict(values); nan_value[model.features[0]] = float("nan")
    with pytest.raises(FrozenRiskModelError, match="not finite"):
        model.predict(nan_value)
