# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #40: document the ten variables read by
src/config.py that were missing from doc/ENV_VARIABLES.md.

Only the mechanically checkable criteria are automated here (1, 3 and the
default-value part of 4). Expected defaults and ranges are read from the
``_env_int(...)`` and ``os.getenv(...)`` calls in src/config.py, so the test
compares the document with the code, as the criteria require.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "doc" / "ENV_VARIABLES.md"
CONFIG = ROOT / "src" / "config.py"

NUMERIC_VARS = [
    "RATE_LIMIT_MESSAGES",
    "RATE_LIMIT_WINDOW",
    "RATE_LIMIT_STALE_TTL",
    "RATE_LIMIT_CLEANUP_INT",
    "WS_AUTH_MAX_ATTEMPTS",
    "WS_AUTH_WINDOW",
    "WS_RECEIVE_TIMEOUT",
]
PATH_VARS = ["TESSERACT_CMD", "POPPLER_PATH"]
ALL_VARS = NUMERIC_VARS + ["WS_API_KEY"] + PATH_VARS


def _config_calls():
    """Map variable name -> list of literal arguments of its _env_int / os.getenv call."""
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    calls = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in ("_env_int", "getenv"):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            calls[first.value] = [
                a.value if isinstance(a, ast.Constant) else None for a in node.args[1:]
            ]
    return calls


def _python_section():
    """Text of the 'Python `.env` Variables' part, up to the next level-2 heading."""
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"^## Python `\.env` Variables\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "Section '## Python `.env` Variables' not found in doc/ENV_VARIABLES.md"
    return match.group(1)


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _find_row(var):
    """Return (header_cells, row_cells) of the table row documenting ``var``
    inside the Python section, or (None, None)."""
    header = None
    previous_is_table = False
    for line in _python_section().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            previous_is_table = False
            continue
        cells = _cells(stripped)
        if not previous_is_table:
            header = cells
        previous_is_table = True
        if cells and cells[0].strip("`* ") == var:
            return header, cells
    return None, None


def _ints(text):
    """Integers in ``text``, tolerating thousands separators like 10,000 / 10'000 / 10_000."""
    normalized = re.sub(r"(?<=\d)[,'_](?=\d{3}\b)", "", text)
    return {int(n) for n in re.findall(r"\d+", normalized)}


# ---------------------------------------------------------------------------
# Criterion 1: each name appears in a table row in the Python `.env` part
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", ALL_VARS)
def test_ac1_variable_has_table_row_in_python_section(var):
    header, row = _find_row(var)
    assert row is not None, (
        f"{var} has no table row in the 'Python `.env` Variables' part of doc/ENV_VARIABLES.md"
    )


# ---------------------------------------------------------------------------
# Criterion 3: numeric default, minimum and maximum identical to _env_int(...)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", NUMERIC_VARS)
def test_ac3_numeric_default_and_range_match_config(var):
    calls = _config_calls()
    assert var in calls, f"{var} is no longer read in src/config.py"
    default, lo, hi = calls[var][:3]
    header, row = _find_row(var)
    assert row is not None, f"{var} has no table row in the Python section"

    lowered = [h.lower() for h in header]
    assert "default" in lowered, f"Table holding {var} has no 'Default' column"
    default_cell = row[lowered.index("default")]
    assert int(default) in _ints(default_cell), (
        f"{var}: documented default {default_cell!r} does not contain {default}"
    )

    row_ints = _ints(" | ".join(row[1:]))
    assert lo in row_ints, f"{var}: minimum {lo} not stated in its row"
    assert hi in row_ints, f"{var}: maximum {hi} not stated in its row"


# ---------------------------------------------------------------------------
# Criterion 4 (default part): OCR path defaults identical to os.getenv(...)
# The "OCR disabled with a warning" statement is prose and is checked by review.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", PATH_VARS)
def test_ac4_ocr_path_default_matches_config(var):
    calls = _config_calls()
    assert var in calls, f"{var} is no longer read in src/config.py"
    default = calls[var][0]
    header, row = _find_row(var)
    assert row is not None, f"{var} has no table row in the Python section"

    lowered = [h.lower() for h in header]
    assert "default" in lowered, f"Table holding {var} has no 'Default' column"
    default_cell = row[lowered.index("default")].replace("`", "")
    assert default_cell == default, (
        f"{var}: documented default {default_cell!r} differs from src/config.py {default!r}"
    )
