import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expected_files(root: Path) -> list[Path]:
    return ([root / "ml/data/raw/annual" / f"ext-journal-{year}.csv" for year in range(2019, 2027)]
            + [root / "ml/data/raw/reference/channels.csv", root / "ml/data/raw/reference/objects.csv"])


def parse_args():
    parser = argparse.ArgumentParser(description="Check that required raw datasets are present and readable.")
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser.parse_args()


def main():
    args = parse_args()
    missing = []
    for path in expected_files(args.root.resolve()):
        state = "OK" if path.is_file() else "MISSING"
        print(f"{state:7} {path}")
        if state == "MISSING": missing.append(path)
    if missing: raise SystemExit(f"Missing {len(missing)} required data files")
    print("Dataset file set is complete.")


if __name__ == "__main__": main()
