from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from backend.app.services.demo_feature_state import DEFAULT_MANIFEST
from backend.app.services.runtime_ml import build_operational_cycle_service


def parse_args():
    parser = argparse.ArgumentParser(description="Run one canonical cycle for the bounded demo fixture.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--snapshot-ts", help="Override the canonical snapshot from the manifest.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not os.environ.get("TGOAT_FEATURE_STATE_DB"):
        raise SystemExit("Set TGOAT_FEATURE_STATE_DB to the SQLite DB created by bootstrap_demo_feature_state.py")
    history_path = os.environ.get("TGOAT_PREDICTION_HISTORY_DB", ":memory:")
    if history_path == ":memory:":
        raise SystemExit("Set TGOAT_PREDICTION_HISTORY_DB to a persistent SQLite path for the demo cycle")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    # Keep the CLI bounded to the digest-pinned fixture even if a broader
    # worker scope was exported in the caller's environment.
    os.environ["TGOAT_OPERATIONAL_OBJECT_IDS"] = str(manifest["object_id"])
    snapshot = args.snapshot_ts or manifest["snapshot_ts"]
    result = build_operational_cycle_service().run_once(
        snapshot_ts=datetime.fromisoformat(snapshot.replace("Z", "+00:00")),
        run_mode="demo",
    )
    print(json.dumps(asdict(result), default=lambda value: value.isoformat(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
