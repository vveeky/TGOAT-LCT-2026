import sys
import json

import pytest

from scripts import export_reference_json
from backend.app.services.reference_data import get_objects_data, get_reference_meta


def write_references(tmp_path, duplicate=False):
    channels = tmp_path / "channels.csv"
    objects = tmp_path / "objects.csv"
    extra = "2866,security,motion,tag,sensor,28\n" if duplicate else ""
    channels.write_text(
        "ид_канала_данных,тип_инж_системы,тип_датчика,тег_инженерной_системы,название_датчика,ид_объект\n"
        "2866,security,motion,tag,sensor,28\n" + extra, encoding="utf-8")
    objects.write_text(
        "ид_объект,иерархия_уровень,родитель,вид_объекта,диспетчерское_название_объекта\n"
        "28,3,9,guardObject,object\n", encoding="utf-8")
    return channels, objects


def run_export(monkeypatch, channels, objects, output):
    monkeypatch.setattr(sys, "argv", ["export_reference_json.py", "--channels", str(channels),
                                      "--objects", str(objects), "--output-dir", str(output)])
    export_reference_json.main()


def test_reference_export_is_deterministic(tmp_path, monkeypatch):
    channels, objects = write_references(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    run_export(monkeypatch, channels, objects, first)
    run_export(monkeypatch, channels, objects, second)
    for name in ("channels.json", "objects.json", "reference_meta.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_reference_export_preserves_broken_parent_semantics(tmp_path, monkeypatch):
    channels, objects = write_references(tmp_path)
    objects.write_text(objects.read_text() + "5773,1,3831,district,broken\n")
    output = tmp_path / "output"
    run_export(monkeypatch, channels, objects, output)
    exported = {x["object_id"]: x for x in json.loads((output / "objects.json").read_text())}
    assert exported[5773]["source_parent_id"] == 3831
    assert exported[5773]["parent_id"] is None
    assert exported[5773]["parent_reference_found"] is False
    assert json.loads((output / "reference_meta.json").read_text())["unresolved_parent_refs"] == 2


def test_reference_export_rejects_duplicate_ids(tmp_path, monkeypatch):
    channels, objects = write_references(tmp_path, duplicate=True)
    with pytest.raises(ValueError, match="Duplicate channel_id"):
        run_export(monkeypatch, channels, objects, tmp_path / "output")


def test_committed_reference_has_exact_known_broken_parent_count():
    assert len(get_objects_data()) == 95
    assert get_reference_meta()["unresolved_parent_refs"] == 1
    broken = [x for x in get_objects_data() if not x["parent_reference_found"]]
    assert [(x["object_id"], x["source_parent_id"], x["parent_id"]) for x in broken] == [
        (5773, 3831, None)
    ]
