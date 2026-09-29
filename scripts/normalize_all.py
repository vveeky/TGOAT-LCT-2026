import argparse
from pathlib import Path
import subprocess
import sys

from normalization import output_is_current

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "ml/data/raw/annual"
OUTPUT_DIR = ROOT / "ml/data/processed"
CHANNELS = ROOT / "ml/data/raw/reference/channels.csv"
OBJECTS = ROOT / "ml/data/raw/reference/objects.csv"
NORMALIZER = ROOT / "scripts/normalize_events.py"


def parse_args():
    parser = argparse.ArgumentParser(description="Normalize all annual files, rebuilding stale outputs.")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for year in range(2019, 2027):
        source = RAW_DIR / f"ext-journal-{year}.csv"
        output = OUTPUT_DIR / f"events_{year}.parquet"
        if not source.is_file(): raise FileNotFoundError(source)
        current, reason = output_is_current(source, output, year, CHANNELS, OBJECTS)
        if current and not args.force:
            print(f"[{year}] current; skipped")
            continue
        print(f"[{year}] rebuilding: {'forced' if args.force else reason}")
        subprocess.run([sys.executable, str(NORMALIZER), "--input", str(source),
                        "--output", str(output), "--year", str(year)], check=True)


if __name__ == "__main__": main()
