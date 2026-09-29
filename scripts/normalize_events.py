import argparse
from pathlib import Path

from normalization import normalize_file

ROOT = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description="Normalize an annual Moscollector CSV atomically.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--channels", type=Path, default=ROOT / "ml/data/raw/reference/channels.csv")
    parser.add_argument("--objects", type=Path, default=ROOT / "ml/data/raw/reference/objects.csv")
    return parser.parse_args()


def main():
    args = parse_args()
    for path in (args.input, args.channels, args.objects):
        if not path.is_file(): raise FileNotFoundError(path)
    metadata = normalize_file(args.input.resolve(), args.output.resolve(), args.year,
                              args.channels.resolve(), args.objects.resolve(), args.limit)
    print(f"Written atomically: {args.output}")
    print(f"Rows: {metadata['rows']:,}; invalid alarm values: {metadata['invalid_alarm_rows']:,}")


if __name__ == "__main__": main()
