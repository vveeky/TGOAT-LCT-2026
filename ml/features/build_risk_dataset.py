"""Canonical entrypoint for the staged Phase 1 risk dataset build."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "ml" / "data" / "processed"
OUTPUT_DIR = ROOT / "ml" / "data" / "features" / "risk_baseline_v1"
MANIFEST = ROOT / "ml" / "artifacts" / "risk_baseline_v1" / "manifest.json"

SYSTEM_TYPES = (
    ("gas", "Газовая охрана"),
    ("security", "Охранная подсистема"),
    ("dispatch", "Диспетчерский контроль"),
    ("fire", "Пожарная охрана"),
    ("temperature", "Температурная подсистема"),
    ("diagnostic", "Диагностическая подсистема"),
)


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build(*, reuse_hourly: bool = False) -> dict:
    """Run the only supported staged generation path."""
    from .staged import build_staged

    return build_staged(reuse_hourly=reuse_hourly)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-hourly", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(reuse_hourly=args.reuse_hourly), ensure_ascii=False, indent=2))
