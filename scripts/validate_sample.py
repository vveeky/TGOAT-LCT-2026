import argparse
from pathlib import Path

try:
    from scripts.normalization import validate_parquet
except ModuleNotFoundError:  # Direct execution from scripts/.
    from normalization import validate_parquet

ROOT = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description="Validate a normalized sample Parquet file.")
    parser.add_argument("--path", type=Path, default=ROOT / "ml/data/processed/events_sample.parquet")
    parser.add_argument("--year", type=int, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    print(validate_parquet(args.path, args.year))


if __name__ == "__main__": main()
