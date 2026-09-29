"""Run a bounded six-hour real-model replay and write diagnostic evidence."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.config import as_utc
from backend.app.services.replay_runtime import (
    DEFAULT_MANIFEST, ReplayRuntime, load_replay_event_source, load_replay_manifest,
)


def wait_for_step(runtime: ReplayRuntime) -> None:
    worker = runtime._step_thread
    if worker is not None:
        worker.join(timeout=900)
        if worker.is_alive():
            raise RuntimeError("bounded six-hour replay exceeded 15 minutes")


def main() -> None:
    manifest, rows = load_replay_manifest(DEFAULT_MANIFEST)
    events = load_replay_event_source(manifest, DEFAULT_MANIFEST)
    runtime = ReplayRuntime(manifest, rows, events=events)
    try:
        baseline = {}
        for object_id in runtime.covered_ids:
            history = runtime.context["forecast"].history(object_id, limit=1)
            if history:
                prediction = history[0].prediction
                baseline[object_id] = {
                    "risk_probability": prediction.risk_probability,
                    "risk_flag": bool(prediction.risk_flag),
                    "prediction_identity": history[0].identity,
                }
        started = time.perf_counter()
        accepted = runtime.step()
        wait_for_step(runtime)
        elapsed = time.perf_counter() - started
        status = runtime.status()
        scored_rows = list(runtime._scoring_rows)
        if status["processed_scoring_boundaries"] != 71:
            raise RuntimeError(f"expected 71 five-minute boundaries, got {status['processed_scoring_boundaries']}")
        if len(scored_rows) != 71 * len(runtime.covered_ids):
            raise RuntimeError(f"unexpected score-row count {len(scored_rows)}")

        by_object: dict[int, list[dict]] = {object_id: [] for object_id in runtime.covered_ids}
        for row in scored_rows:
            by_object[row["object_id"]].append(row)
        transitions = Counter()
        near_hour_transitions = 0
        for object_id, object_rows in by_object.items():
            previous = baseline.get(object_id)
            for row in object_rows:
                current = {"risk_flag": bool(row["risk_flag"]), **row}
                if previous is not None and previous["risk_flag"] != current["risk_flag"]:
                    key = "positive_to_negative" if previous["risk_flag"] else "negative_to_positive"
                    transitions[key] += 1
                    local_time = as_utc(datetime.fromisoformat(row["snapshot_ts"])).astimezone(
                        ZoneInfo("Europe/Moscow")
                    )
                    minutes_from_hour = min(local_time.minute, 60 - local_time.minute)
                    if minutes_from_hour <= 10:
                        near_hour_transitions += 1
                previous = current

        cards_by_object = {
            object_id: len(runtime.context["forecast"].cards(object_id))
            for object_id in runtime.covered_ids
        }
        signal_total = runtime.context["workflow"].signals()["total"]
        card_total = sum(cards_by_object.values())
        if signal_total != card_total:
            raise RuntimeError(f"signal/card count mismatch: signals={signal_total}, cards={card_total}")

        benchmark_time = as_utc(runtime.start_at + timedelta(hours=6, minutes=-5))
        inference = runtime.context["cycle"].inference_service
        benchmark = []
        covered = sorted(runtime.covered_ids)
        for count in (1, 10, 50, len(covered)):
            ids = covered[:count]
            started_batch = time.perf_counter()
            results = inference.batch_predict([(object_id, benchmark_time) for object_id in ids])
            duration = time.perf_counter() - started_batch
            if len(results) != len(ids):
                raise RuntimeError(f"benchmark batch cardinality mismatch for {count}")
            statuses = Counter(result["status"] for result in results)
            if statuses != {"available": count}:
                raise RuntimeError(f"benchmark batch was not all real inference: {dict(statuses)}")
            benchmark.append({
                "objects": count, "predictions": len(results), "elapsed_seconds": duration,
                "scores_per_second": len(results) / duration if duration else None,
                "status_counts": dict(statuses),
            })

        timeline_object = 5343
        timeline = []
        for row in by_object.get(timeline_object, []):
            timeline.append({
                key: row[key] for key in (
                    "snapshot_ts", "reason", "event_count_since_last_score", "risk_probability",
                    "risk_flag", "status", "forecast_card_id", "forecast_card_status",
                    "prediction_identity", "terminal",
                )
            })
        duplicate_rows = runtime.context["workflow"].conn.execute(
            "SELECT COUNT(*) FROM (SELECT card_id FROM signal_work GROUP BY card_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        duplicates = int(duplicate_rows)
        report = {
            "status": "PASS",
            "replay_id": manifest["replay_id"],
            "start_time": runtime.start_at.isoformat(),
            "step_target": accepted["pending_step_target"],
            "processed_through": status["virtual_time"],
            "step_state": status["state"],
            "scoring_cadence_seconds": status["scoring_interval_seconds"],
            "processed_boundaries": status["processed_scoring_boundaries"],
            "covered_objects_scored_each_boundary": len(runtime.covered_ids),
            "prediction_records_created": len(scored_rows),
            "step_elapsed_seconds": elapsed,
            "max_boundary_seconds": status["max_boundary_duration_seconds"],
            "telemetry_and_time_records": sum(row["reason"] == "telemetry_and_time" for row in scored_rows),
            "time_only_records": sum(row["reason"] == "time_driven_recency" for row in scored_rows),
            "flag_transitions": dict(transitions),
            "flag_transitions_near_hour_boundary_within_10m": near_hour_transitions,
            "forecast_card_episodes": card_total,
            "human_signals": signal_total,
            "duplicate_signals_by_card": duplicates,
            "object_4768_coverage_exclusion": 4768 not in runtime.covered_ids,
            "score_throughput_benchmarks": benchmark,
            "object_5343_timeline": timeline,
            "artifact_sha256": manifest["artifact_sha256"],
            "feature_count": manifest["feature_count"],
        }
        path = DEFAULT_MANIFEST.parent / "replay_6h_scoring_evidence.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["evidence_file"] = str(path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if duplicates:
            raise SystemExit("duplicate signals detected")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
