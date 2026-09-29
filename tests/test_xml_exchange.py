from xml.etree import ElementTree

import pytest

from backend.app.services.xml_exchange import prefers_xml, xml_response


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        (None, False),
        ("*/*", False),
        ("application/json", False),
        ("application/xml", True),
        ("text/xml", True),
        ("application/json, application/xml", False),
        ("application/xml, application/json", True),
        ("application/xml;q=0.2, application/json;q=0.8", False),
        ("application/xml;q=0.8, application/json;q=0.2", True),
        ("application/xml;q=0, application/json", False),
        ("application/xml;q=1.2, application/json", False),
        ("application/xml;q=broken, application/json", False),
    ],
)
def test_accept_header_selects_xml_only_when_explicitly_preferred(accept, expected):
    assert prefers_xml(accept) is expected


def test_xml_response_escapes_text_and_marks_nulls():
    response = xml_response(
        {"message": "A & B < C", "available": False, "value": None},
        "operational_dashboard",
    )

    assert response.media_type == "application/xml"
    assert response.headers["vary"] == "Accept"
    root = ElementTree.fromstring(response.body)
    assert root.findtext("message") == "A & B < C"
    assert root.findtext("available") == "false"
    assert root.find("value").get("nil") == "true"
