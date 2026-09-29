from collections import Counter
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_customer_confirmed_roles_are_separated_from_product_personas():
    roles = (ROOT / "ROLE_CAPABILITY_MATRIX.md").read_text(encoding="utf-8")
    matrix = (ROOT / "TZ_COMPLIANCE_MATRIX.md").read_text(encoding="utf-8")
    assert "information-system administrator exists" in roles
    assert "roles are cumulative" in roles
    assert "roles[] + explicit grants[] + groups[] + resource scope" in roles
    assert "| Manager persona |" in roles and "| Analyst persona |" in roles
    assert "cumulative roles" in matrix


def test_compliance_matrix_status_totals_match_documented_snapshot():
    matrix = (ROOT / "TZ_COMPLIANCE_MATRIX.md").read_text(encoding="utf-8")
    counts = Counter(re.findall(
        r"\| (PASS|PARTIAL|NOT_IMPLEMENTED|BLOCKED_EXTERNAL|OPTIONAL|NOT_APPLICABLE) \|\s*$",
        matrix, re.MULTILINE,
    ))
    assert counts == {
        "PASS": 13, "PARTIAL": 38, "NOT_IMPLEMENTED": 1,
        "BLOCKED_EXTERNAL": 1, "OPTIONAL": 7,
    }
    assert "Corrected Stage 6.1 totals: 3 `PASS`, 41 `PARTIAL`, 8 `NOT_IMPLEMENTED`" in matrix
    assert "`PASS` 13, `PARTIAL` 38, `NOT_IMPLEMENTED` 1" in matrix
