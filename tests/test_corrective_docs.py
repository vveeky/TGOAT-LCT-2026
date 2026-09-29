from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_spec_describes_truthful_synthetic_geometry_policy():
    spec = (ROOT / "docs/DASHBOARD_SPEC.md").read_text(encoding="utf-8")
    assert "geometry_source=synthetic_mvp" in spec
    assert "реальным `object_id`" in spec
    assert "не выдаётся за фактические координаты" in spec


def test_plan_does_not_claim_concurrency_or_load_tests_as_completed():
    plan = (ROOT / "docs/BACKEND_DATA_CONTRACT_FINALIZATION_PLAN.md").read_text(
        encoding="utf-8"
    )
    assert "multi-user concurrent writes and 20-user load have not been tested" in plan
    assert "Production concurrency/load test не проведён" in plan
    assert "## Демонстрация действий разных пользователей" in plan


def test_plan_audit_policy_requires_full_safe_request_audit():
    plan = (ROOT / "docs/BACKEND_DATA_CONTRACT_FINALIZATION_PLAN.md").read_text(
        encoding="utf-8"
    )
    assert "Не логировать каждое GET-чтение" not in plan
    assert "Все пользовательские business API actions, включая reads" in plan
    assert "Login attempts журналируются" in plan
    assert "Meaningful writes дополнительно создают domain events" in plan
    assert "audit storage — in-memory" in plan
    assert "20-user load/concurrency test ещё не проведён" in plan


def test_dashboard_and_api_docs_match_frozen_backend_contract():
    dashboard = (ROOT / "docs/DASHBOARD_SPEC.md").read_text(encoding="utf-8")
    contract = (ROOT / "docs/API_CONTRACT.md").read_text(encoding="utf-8")
    assert "active requests. Последнее событие доступно на уровне Channel" in dashboard
    assert "## `/maintenance-requests`" in dashboard
    assert "## `/maintenance`" not in dashboard
    assert "визуализирует geometry в соответствии с её GeoJSON type" in dashboard
    assert "если `prediction_id == null`, уведомление остаётся system/global message" in dashboard
    assert "Create request принимает только" in contract
    assert "Maintenance patch принимает только обязательный допустимый `status`" in contract
