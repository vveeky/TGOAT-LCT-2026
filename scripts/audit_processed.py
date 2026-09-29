import argparse
from pathlib import Path

import polars as pl

try:
    from scripts.normalization import output_is_current, validate_parquet
except ModuleNotFoundError:  # Direct execution from scripts/.
    from normalization import output_is_current, validate_parquet

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_YEARS = list(range(2019, 2027))


def parse_args():
    parser = argparse.ArgumentParser(description="Audit normalized annual Parquet files.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "ml/data/processed")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "ml/data/raw/annual")
    parser.add_argument("--channels", type=Path, default=ROOT / "ml/data/raw/reference/channels.csv")
    parser.add_argument("--objects", type=Path, default=ROOT / "ml/data/raw/reference/objects.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/data/interim/processed_audit.csv")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Allow missing annual partitions, but never stale/corrupt partitions.")
    return parser.parse_args()


def audit_file(path: Path, year: int) -> pl.DataFrame:
    validate_parquet(path, year)
    frame = pl.scan_parquet(path)
    return frame.select(
        pl.lit(year).alias("year"), pl.len().alias("rows"),
        pl.col("channel_id").n_unique().alias("unique_channels"),
        pl.col("object_id").filter(pl.col("object_reference_found")).n_unique().alias("mapped_objects"),
        (~pl.col("channel_reference_found")).sum().alias("missing_channel_rows"),
        pl.col("is_alarm").fill_null(False).sum().alias("alarm_rows"),
        pl.col("invalid_alarm").sum().alias("invalid_alarm_rows"),
        pl.col("timestamp").min().alias("first_timestamp"),
        pl.col("timestamp").max().alias("last_timestamp"),
        pl.col("invalid_event_id").sum().alias("invalid_event_ids"),
        pl.col("invalid_channel_id").sum().alias("invalid_channel_ids"),
        pl.col("invalid_timestamp").sum().alias("invalid_timestamps"),
    ).collect()


def run_audit(data_dir: Path, raw_dir: Path, channels: Path, objects: Path,
              *, allow_partial: bool) -> tuple[pl.DataFrame, list[int]]:
    available = [year for year in EXPECTED_YEARS
                 if (data_dir / f"events_{year}.parquet").is_file()]
    missing = sorted(set(EXPECTED_YEARS) - set(available))
    if missing and not allow_partial:
        raise ValueError(f"Missing required years: {missing}. Use --allow-partial explicitly.")
    if not available:
        raise ValueError("No annual Parquet files found")
    stale = []
    for year in available:
        source = raw_dir / f"ext-journal-{year}.csv"
        output = data_dir / f"events_{year}.parquet"
        current, reason = output_is_current(source, output, year, channels, objects)
        if not current:
            stale.append(f"{year}: {reason}")
    if stale:
        raise ValueError("Stale or invalid partitions: " + "; ".join(stale))
    return pl.concat([audit_file(data_dir / f"events_{year}.parquet", year)
                      for year in available]), missing


def main():
    args = parse_args()
    try:
        summary, missing = run_audit(
            args.data_dir, args.raw_dir, args.channels, args.objects,
            allow_partial=args.allow_partial,
        )
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        raise SystemExit(str(error)) from error
    if missing:
        print(f"WARNING: explicitly allowed missing years: {missing}")
    print(summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary.write_csv(args.output)
    print(f"Audit written: {args.output}")


if __name__ == "__main__":
    main()
