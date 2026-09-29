from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.services.demo_feature_state import (
    DEFAULT_MANIFEST,
    bootstrap_demo_feature_state,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a bounded SQLite feature-state DB from the checked-in demo event fixture."
    )
    parser.add_argument("--output", type=Path, required=True,
                        help="New SQLite path; an existing file is never overwritten.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="Demo source manifest (defaults to the checked-in, digest-pinned fixture).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = bootstrap_demo_feature_state(args.output, manifest_path=args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
