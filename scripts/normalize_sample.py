"""Normalize the local sample through the same core as annual production files."""
from pathlib import Path
import argparse

try:
    from scripts.normalization import normalize_file
except ModuleNotFoundError:  # Direct execution from scripts/.
    from normalization import normalize_file

ROOT = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description="Normalize a sample with the production core.")
    parser.add_argument("--input", type=Path, default=ROOT / "ml/data/raw/sample/events_sample.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/data/processed/events_sample.parquet")
    parser.add_argument("--year", type=int, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    metadata = normalize_file(
        args.input, args.output, args.year,
        ROOT / "ml/data/raw/reference/channels.csv",
        ROOT / "ml/data/raw/reference/objects.csv",
    )
    print(f"Sample normalized with production pipeline: {metadata['rows']:,} rows")


if __name__ == "__main__": main()
