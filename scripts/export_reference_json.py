from pathlib import Path
import json
import argparse
import hashlib

import polars as pl


ROOT = Path(__file__).resolve().parents[1]

CHANNELS_CSV = ROOT / "ml/data/raw/reference/channels.csv"
OBJECTS_CSV = ROOT / "ml/data/raw/reference/objects.csv"

OUTPUT_DIR = ROOT / "backend/data"


def write_json(path: Path, data):
    with path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Generate deterministic backend reference JSON.")
    parser.add_argument("--channels", type=Path, default=CHANNELS_CSV)
    parser.add_argument("--objects", type=Path, default=OBJECTS_CSV)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    channels = (
        pl.read_csv(args.channels)
        .rename(
            {
                "ид_канала_данных": "channel_id",
                "тип_инж_системы": "system_type",
                "тип_датчика": "sensor_type",
                "тег_инженерной_системы": "system_tag",
                "название_датчика": "sensor_name",
                "ид_объект": "object_id",
            }
        )
        .with_columns(
            pl.col("channel_id").cast(pl.Int64),
            pl.col("object_id").cast(pl.Int64),
        )
        .sort("channel_id")
    )

    objects = (
        pl.read_csv(args.objects)
        .rename(
            {
                "ид_объект": "object_id",
                "иерархия_уровень": "object_level",
                "родитель": "parent_id",
                "вид_объекта": "object_type",
                "диспетчерское_название_объекта": "object_name",
            }
        )
        .with_columns(
            pl.col("object_id").cast(pl.Int64),
            pl.col("parent_id").cast(pl.Int64, strict=False).alias("source_parent_id"),
        )
        .drop("parent_id")
    )

    channel_counts = (
        channels
        .group_by("object_id")
        .len()
        .rename({"len": "channels_count"})
    )

    objects = (
        objects
        .join(
            channel_counts,
            on="object_id",
            how="left",
        )
        .with_columns(
            pl.col("channels_count")
            .fill_null(0)
            .cast(pl.Int64)
        )
        .with_columns(
            (pl.col("channels_count") > 0)
            .alias("has_channels")
        )
        .sort("object_id")
    )

    if channels["channel_id"].n_unique() != channels.height:
        raise ValueError("Duplicate channel_id in reference")
    if objects["object_id"].n_unique() != objects.height:
        raise ValueError("Duplicate object_id in reference")
    unknown_objects = channels.join(objects.select("object_id"), on="object_id", how="anti")
    if unknown_objects.height:
        raise ValueError(f"{unknown_objects.height} channels reference unknown objects")

    object_ids = objects["object_id"].implode()
    objects = objects.with_columns(
        (pl.col("source_parent_id").is_null() | pl.col("source_parent_id").is_in(object_ids))
        .alias("parent_reference_found"),
    ).with_columns(
        pl.when(pl.col("parent_reference_found")).then(pl.col("source_parent_id"))
        .otherwise(None).cast(pl.Int64).alias("parent_id")
    )
    unresolved_parent_refs = objects.filter(~pl.col("parent_reference_found")).height

    reference_hash = hashlib.sha256(args.channels.read_bytes() + args.objects.read_bytes()).hexdigest()

    meta = {
        "channels_total": channels.height,
        "objects_total": objects.height,
        "objects_with_channels": objects.filter(
            pl.col("has_channels")
        ).height,
        "unresolved_parent_refs": unresolved_parent_refs,
        "schema_version": 2,
        "source_sha256": reference_hash,
    }

    write_json(
        args.output_dir / "channels.json",
        channels.to_dicts(),
    )

    write_json(
        args.output_dir / "objects.json",
        objects.to_dicts(),
    )

    write_json(
        args.output_dir / "reference_meta.json",
        meta,
    )

    print("=== REFERENCE EXPORT ===")
    print(f"Channels: {channels.height:,}")
    print(f"Objects total: {objects.height:,}")
    print(
        "Objects with channels: "
        f"{meta['objects_with_channels']:,}"
    )
    print()
    print(args.output_dir / "channels.json")
    print(args.output_dir / "objects.json")
    print(args.output_dir / "reference_meta.json")


if __name__ == "__main__":
    main()
