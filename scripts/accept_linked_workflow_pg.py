#!/usr/bin/env python3
"""Run one linked forecast -> signal -> request acceptance trace in disposable PG.

Requires TGOAT_OPERATIONAL_DATABASE_URL and a fresh disposable database whose
name ends in ``_review``. First advance the pinned simulated-live source once
with scripts/accept_simulated_live_pg.py --advance. This script then creates
one inspection request from a real positive forecast, exercises its lifecycle,
and verifies the linked rows after rebuilding the runtime/database pool.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


SOURCE_ID = "simulated-live:moscow-2026-05-05-demo-v1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-disposable-db", action="store_true",
                        help="confirm that the target PostgreSQL database is disposable")
    parser.add_argument("--output", type=Path, default=Path("/tmp/tgoat-linked-e2e-trace.json"))
    args = parser.parse_args()
    database_url = os.environ.get("TGOAT_OPERATIONAL_DATABASE_URL", "")
    database_name = urlsplit(database_url).path.lstrip("/")
    if not database_url:
        parser.error("TGOAT_OPERATIONAL_DATABASE_URL is required")
    if not args.confirm_disposable_db or not database_name.endswith("_review"):
        parser.error("requires --confirm-disposable-db and a disposable database ending in _review")

    from backend.app.services.auth import AuthUser
    from backend.app.services.runtime_ml import build_postgres_operational_runtime

    runtime = build_postgres_operational_runtime()
    database = runtime["database"]
    workflow = runtime["workflow"]
    try:
        with database.connection() as connection:
            progress = connection.execute(
                "SELECT source_digest,processed_at FROM simulated_live_progress WHERE source_id=%s",
                (SOURCE_ID,),
            ).fetchone()
            if not progress:
                raise RuntimeError("Run one simulated-live tick before this linked workflow check")
            source_digest, processed_at = progress
            candidate = connection.execute(
                """SELECT p.identity,p.object_id,p.snapshot_ts,p.risk_probability,p.threshold,
                    p.model_version,p.feature_schema_version,p.reference_snapshot_id,p.reference_hash,
                    c.card_id,c.status AS forecast_card_status
                    FROM prediction_history p
                    JOIN forecast_card_predictions m ON m.prediction_identity=p.identity
                    JOIN forecast_cards c ON c.card_id=m.card_id
                    WHERE p.snapshot_ts=%s AND p.risk_flag IS TRUE AND p.prediction_status='available'
                    ORDER BY p.risk_probability DESC,p.object_id LIMIT 1""",
                (processed_at,),
            ).fetchone()
            if not candidate:
                raise RuntimeError("No positive simulated-live forecast is linked to a forecast card")
            identity, object_id, snapshot, probability, threshold, model_version, feature_version, \
                reference_id, reference_hash, card_id, card_status = candidate
            existing = connection.execute(
                """SELECT EXISTS(SELECT 1 FROM signal_decisions WHERE card_id=%s),
                    EXISTS(SELECT 1 FROM maintenance_requests WHERE card_id=%s)""",
                (card_id, card_id),
            ).fetchone()
            if any(existing):
                raise RuntimeError("Selected signal already has workflow activity; use a fresh _review database")
            events_count = connection.execute(
                "SELECT count(*) FROM simulated_live_events WHERE source_id=%s AND timestamp<=%s",
                (SOURCE_ID, processed_at),
            ).fetchone()[0]
            forecast_counts = connection.execute(
                "SELECT count(*),count(*) FILTER (WHERE risk_flag IS TRUE) FROM prediction_history WHERE snapshot_ts=%s",
                (processed_at,),
            ).fetchone()

        central = AuthUser("review-central", "review-central", "central_dispatcher", None)
        technician = AuthUser("review-tech", "review-tech", "technician", (int(object_id),))
        decision_command = {
            "expected_version": 0,
            "idempotency_key": "expert-review-linked-request-20260928",
            "decision": "inspection_requested",
            "prediction_identity": identity,
            "comment": "Functional acceptance trace; no physical incident assertion.",
            "reason_code": "acceptance_test",
            "request": {"reason": "Verify forecast-to-request workflow link", "priority": "normal"},
        }
        decision = workflow.decide(card_id, actor_user_id=central.user_id,
                                   actor_role=central.role, payload=decision_command)
        replay = workflow.decide(card_id, actor_user_id=central.user_id,
                                 actor_role=central.role, payload=decision_command)
        if replay != decision:
            raise AssertionError("Signal command retry did not return its idempotent receipt")

        request_id = decision["request"]["id"]
        version = decision["request"]["version"]
        transitions = [{"action": "inspection_requested", "status": decision["request"]["status"],
                        "version": version}]
        assigned = workflow.request_command(
            request_id, "assign", central,
            {"expected_version": version, "idempotency_key": "expert-review-assign-20260928",
             "assignee_user_id": technician.user_id}, directory=(technician,),
        )
        version = assigned["version"]
        transitions.append({"action": "assign", "status": assigned["status"], "version": version})
        started = workflow.request_command(
            request_id, "start", technician,
            {"expected_version": version, "idempotency_key": "expert-review-start-20260928"},
        )
        version = started["version"]
        transitions.append({"action": "start", "status": started["status"], "version": version})
        submitted = workflow.request_command(
            request_id, "result", technician,
            {"expected_version": version, "idempotency_key": "expert-review-result-20260928",
             "result_code": "unable_to_verify",
             "summary": "Acceptance workflow only; no physical inspection was performed and no physical incident conclusion was made."},
        )
        version = submitted["version"]
        transitions.append({"action": "result", "status": submitted["status"], "version": version})
        accepted = workflow.request_command(
            request_id, "accept", central,
            {"expected_version": version, "idempotency_key": "expert-review-accept-20260928",
             "comment": "Workflow verification only; no physical incident conclusion."},
        )
        transitions.append({"action": "accept", "status": accepted["status"], "version": accepted["version"]})
        detail = workflow.request_detail(request_id, central)
        if (detail["status"] != "closed" or detail["prediction_identity"] != identity
                or detail["card_id"] != card_id
                or detail["latest_result"]["result_code"] != "unable_to_verify"):
            raise AssertionError("Request linkage or lifecycle did not match the selected forecast and signal")
        event_names = [event["event"] for event in detail["timeline"]]
    finally:
        database.close()

    # Recreate the runtime and verify all linked records survived the restart.
    restarted = build_postgres_operational_runtime()
    try:
        with restarted["database"].connection() as connection:
            persisted = connection.execute(
                """SELECT p.identity,m.card_id,c.object_id,d.id AS decision_id,
                    d.prediction_identity AS decision_prediction_identity,r.id AS request_id,
                    r.prediction_identity AS request_prediction_identity,r.card_id AS request_card_id,
                    r.status AS request_status,r.version AS request_version,count(DISTINCT e.id) AS event_count
                    FROM prediction_history p
                    JOIN forecast_card_predictions m ON m.prediction_identity=p.identity
                    JOIN forecast_cards c ON c.card_id=m.card_id
                    JOIN signal_decisions d ON d.card_id=m.card_id
                    JOIN maintenance_requests r ON r.card_id=m.card_id AND r.decision_id=d.id
                    LEFT JOIN maintenance_events e ON e.request_id=r.id
                    WHERE p.identity=%s AND r.id=%s
                    GROUP BY p.identity,m.card_id,c.object_id,d.id,d.prediction_identity,r.id,
                             r.prediction_identity,r.card_id,r.status,r.version""",
                (identity, request_id),
            ).fetchone()
        if not persisted:
            raise AssertionError("Linked records disappeared after runtime restart")
        if not (persisted[0] == identity and persisted[1] == card_id
                and persisted[3] == decision["decision_id"] and persisted[4] == identity
                and persisted[5] == request_id and persisted[6] == identity
                and persisted[7] == card_id and persisted[8] == "closed"):
            raise AssertionError("Persisted forecast/signal/request foreign links do not agree")

        trace = {
            "result": "PASS",
            "database": database_name + " (disposable PostgreSQL; no credentials recorded)",
            "source": {"kind": "pinned historical fixture revealed as simulated-live stream",
                       "source_id": SOURCE_ID, "source_sha256": source_digest,
                       "processed_at": processed_at.isoformat(), "ingested_events": events_count,
                       "scored_objects": forecast_counts[0], "positive_forecasts": forecast_counts[1]},
            "forecast": {"prediction_identity": identity, "object_id": object_id,
                         "snapshot_ts": snapshot.isoformat(), "risk_probability": probability,
                         "threshold": threshold, "model_version": model_version,
                         "feature_schema_version": feature_version,
                         "reference_snapshot_id": reference_id, "reference_hash": reference_hash},
            "signal": {"card_id": card_id, "initial_forecast_card_status": card_status,
                       "decision_id": decision["decision_id"], "decision": "inspection_requested",
                       "decision_prediction_identity": persisted[4]},
            "request": {"request_id": request_id, "origin": "signal",
                        "prediction_identity": persisted[6], "card_id": persisted[7],
                        "final_status": persisted[8], "final_version": persisted[9],
                        "event_count": persisted[10]},
            "idempotency": {"signal_command_replay_same_result": True},
            "lifecycle": transitions,
            "timeline_events": event_names,
            "physical_claims": {"inspection_performed": False, "incident_assertion": False},
            "restart_persistence": "PASS",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = json.dumps(trace, ensure_ascii=False, indent=2, default=str)
        args.output.write_text(output + "\n")
        print(output)
    finally:
        restarted["database"].close()


if __name__ == "__main__":
    main()
