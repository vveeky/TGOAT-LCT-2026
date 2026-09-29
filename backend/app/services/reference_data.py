from functools import lru_cache
from pathlib import Path
import json
import re


ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "backend/data"
REFERENCE_FILES = ("objects.json", "channels.json", "reference_meta.json")


class ReferenceDataUnavailableError(RuntimeError):
    pass


def _load_json(name: str):
    try:
        with (DATA_DIR / name).open(encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as error:
        raise ReferenceDataUnavailableError(f"missing {name}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ReferenceDataUnavailableError(f"cannot read {name}: {error}") from error


def _reference_signature() -> tuple:
    return tuple((name, (DATA_DIR / name).stat().st_size, (DATA_DIR / name).stat().st_mtime_ns)
                 for name in REFERENCE_FILES)


def _current_signature() -> tuple:
    try:
        return _reference_signature()
    except OSError as error:
        message = f"missing {error.filename}" if isinstance(error, FileNotFoundError) else str(error)
        raise ReferenceDataUnavailableError(message) from error


@lru_cache(maxsize=4)
def _load_reference_bundle(_: tuple) -> tuple[list[dict], list[dict], dict]:
    objects = _load_json("objects.json")
    channels = _load_json("channels.json")
    meta = _load_json("reference_meta.json")
    if not isinstance(objects, list) or not isinstance(channels, list) or not isinstance(meta, dict):
        raise ReferenceDataUnavailableError("invalid top-level reference structure")
    required_object = {"object_id", "object_name", "object_type", "channels_count", "has_channels",
                       "source_parent_id", "parent_id", "parent_reference_found"}
    required_channel = {"channel_id", "object_id", "sensor_type", "system_type", "sensor_name", "system_tag"}
    if any(not isinstance(x, dict) or not required_object <= x.keys() for x in objects):
        raise ReferenceDataUnavailableError("invalid object structure")
    if any(not isinstance(x, dict) or not required_channel <= x.keys() for x in channels):
        raise ReferenceDataUnavailableError("invalid channel structure")
    object_ids = [x["object_id"] for x in objects]
    channel_ids = [x["channel_id"] for x in channels]
    object_id_set = set(object_ids)
    if len(object_ids) != len(object_id_set):
        raise ReferenceDataUnavailableError("duplicate object_id")
    if len(channel_ids) != len(set(channel_ids)):
        raise ReferenceDataUnavailableError("duplicate channel_id")
    unknown = [x["channel_id"] for x in channels if x["object_id"] not in object_id_set]
    if unknown:
        raise ReferenceDataUnavailableError(f"channels reference unknown objects: {len(unknown)}")
    unresolved = sum(not x["parent_reference_found"] for x in objects)
    expected_meta = {
        "objects_total": len(objects), "channels_total": len(channels),
        "objects_with_channels": sum(bool(x["has_channels"]) for x in objects),
        "unresolved_parent_refs": unresolved, "schema_version": 2,
    }
    for key, expected in expected_meta.items():
        if meta.get(key) != expected:
            raise ReferenceDataUnavailableError(f"reference_meta {key} mismatch")
    if not isinstance(meta.get("source_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", meta["source_sha256"]
    ):
        raise ReferenceDataUnavailableError("invalid reference_meta source_sha256")
    channel_counts: dict[int, int] = {}
    for channel in channels:
        channel_counts[channel["object_id"]] = channel_counts.get(channel["object_id"], 0) + 1
    for obj in objects:
        if obj["channels_count"] != channel_counts.get(obj["object_id"], 0):
            raise ReferenceDataUnavailableError("object channels_count mismatch")
        if obj["has_channels"] is not (obj["channels_count"] > 0):
            raise ReferenceDataUnavailableError("object has_channels mismatch")
        source_parent = obj["source_parent_id"]
        expected_found = source_parent is None or source_parent in object_id_set
        if obj["parent_reference_found"] is not expected_found:
            raise ReferenceDataUnavailableError("invalid parent_reference_found")
        expected_parent = source_parent if expected_found else None
        if obj["parent_id"] != expected_parent:
            raise ReferenceDataUnavailableError("invalid safe parent_id")
    return objects, channels, meta


def get_reference_bundle() -> tuple[list[dict], list[dict], dict]:
    return _load_reference_bundle(_current_signature())


def clear_reference_cache() -> None:
    _load_reference_bundle.cache_clear()
    _derived_indexes.cache_clear()


def reference_data_health() -> dict:
    missing = [name for name in REFERENCE_FILES if not (DATA_DIR / name).is_file()]
    if missing:
        return {"status": "unavailable", "detail": f"missing: {', '.join(missing)}"}
    try:
        get_reference_bundle()
        return {"status": "available", "detail": None}
    except ReferenceDataUnavailableError as error:
        return {"status": "corrupt", "detail": str(error)}


def reference_data_available() -> bool:
    return reference_data_health()["status"] == "available"


def get_objects_data() -> list[dict]: return get_reference_bundle()[0]
def get_channels_data() -> list[dict]: return get_reference_bundle()[1]
def get_reference_meta() -> dict: return get_reference_bundle()[2]


@lru_cache(maxsize=4)
def _derived_indexes(signature: tuple) -> tuple[dict[int, dict], dict[int, dict], dict[int, list[dict]]]:
    objects, channels, _ = _load_reference_bundle(signature)
    result: dict[int, list[dict]] = {}
    for channel in channels:
        result.setdefault(channel["object_id"], []).append(channel)
    return ({obj["object_id"]: obj for obj in objects},
            {channel["channel_id"]: channel for channel in channels}, result)


def get_objects_by_id() -> dict[int, dict]: return _derived_indexes(_current_signature())[0]
def get_channels_by_id() -> dict[int, dict]: return _derived_indexes(_current_signature())[1]
def get_channels_by_object() -> dict[int, list[dict]]: return _derived_indexes(_current_signature())[2]
