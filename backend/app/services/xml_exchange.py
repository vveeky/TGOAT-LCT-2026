"""Small deterministic XML representation for read-only API exchange."""

from __future__ import annotations

from collections.abc import Mapping
from xml.etree import ElementTree

from fastapi.responses import Response
from pydantic import BaseModel


def prefers_xml(accept: str | None) -> bool:
    """Select XML only when explicitly preferred over JSON; default stays JSON."""
    if not accept:
        return False
    preferences: list[tuple[float, int, str]] = []
    for position, part in enumerate(accept.split(",")):
        media_type, *parameters = part.strip().lower().split(";")
        quality = 1.0
        for parameter in parameters:
            name, separator, value = parameter.strip().partition("=")
            if separator and name == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
                if not 0.0 <= quality <= 1.0:
                    quality = 0.0
        if media_type in {"application/xml", "text/xml", "application/json"}:
            preferences.append((quality, -position, media_type))
    if not preferences:
        return False
    best = max(preferences)
    return best[2] in {"application/xml", "text/xml"} and best[0] > 0


def _append(parent: ElementTree.Element, name: str, value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    child = ElementTree.SubElement(parent, name)
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _append(child, str(key), nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _append(child, "item", nested)
    elif value is None:
        child.set("nil", "true")
    elif isinstance(value, bool):
        child.text = "true" if value else "false"
    else:
        child.text = str(value)


def xml_response(payload: BaseModel | Mapping[str, object], root_name: str) -> Response:
    """Encode a validated read-model payload with stable list item names."""
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    root = ElementTree.Element(root_name)
    for key, value in payload.items():
        _append(root, str(key), value)
    return Response(
        content=ElementTree.tostring(root, encoding="utf-8", xml_declaration=True),
        media_type="application/xml",
        headers={"Vary": "Accept"},
    )
