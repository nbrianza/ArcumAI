# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #53: retire the LOOPBACK_TIMEOUT setting, which
was documented but never applied (option A, chosen by the owner).

Criteria 1-4 are automated here. Criterion 5 is the full suite run and
criterion 6 (changelog line) is checked by review.

Tests that need a fresh import of src.bridge.manager or src.config run in a
separate interpreter, so the bridge_manager singleton and the src.config
constants already imported by this test session are never reloaded.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "doc" / "ENV_VARIABLES.md"
WHOLE_WORD = re.compile(r"\bLOOPBACK_TIMEOUT\b")

# Synthetic values only.
SYNTHETIC_TIMEOUT_MS = "120000"
DEFAULT_TIMEOUT_MS = 3600000


def _matches(path):
    hits = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if WHOLE_WORD.search(line):
            hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    return hits


# --- Criterion 1: the name is gone from the sources and the settings reference ---

def test_c1_whole_word_regex_does_not_match_vsto_variable():
    # Guard on the search itself: the add-in setting must not be a false positive.
    assert not WHOLE_WORD.search("VSTO_LOOPBACK_TIMEOUT_MS")
    assert WHOLE_WORD.search("LOOPBACK_TIMEOUT = 1")


def test_c1_no_loopback_timeout_in_src_python_files():
    hits = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        hits.extend(_matches(path))
    assert hits == [], "LOOPBACK_TIMEOUT still referenced:\n" + "\n".join(hits)


def test_c1_no_loopback_timeout_in_main_nice():
    assert _matches(ROOT / "main_nice.py") == []


def test_c1_no_loopback_timeout_in_env_variables_doc():
    assert _matches(DOC) == []


# --- Helpers for criteria 2 and 4: fresh interpreter -----------------------------

_PROBE = r"""
import json
from src.bridge.manager import OutlookBridgeManager
cfg = OutlookBridgeManager()._build_client_config("vsto_outlook")
print("PROBE_RESULT=" + json.dumps(cfg, sort_keys=True, default=str))
"""


_CASES = {
    "unset": {},
    "stale_non_numeric": {"LOOPBACK_TIMEOUT": "abc"},
    "stale_numeric": {"LOOPBACK_TIMEOUT": "42"},
    "vsto_set": {"VSTO_LOOPBACK_TIMEOUT_MS": SYNTHETIC_TIMEOUT_MS},
}

_results_cache: dict = {}


def _probe(case):
    """Import src.bridge.manager in a fresh interpreter per case and return
    (returncode, client config or None, stderr tail). All cases start in
    parallel on first use, because each import of the bridge is slow."""
    if not _results_cache:
        procs = {}
        for label, extra_env in _CASES.items():
            env = {k: v for k, v in os.environ.items()
                   if k not in ("LOOPBACK_TIMEOUT", "VSTO_LOOPBACK_TIMEOUT_MS")}
            env["ARCUMAI_ENV"] = "dev"
            env.update(extra_env)
            procs[label] = subprocess.Popen(
                [sys.executable, "-c", _PROBE], cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        for label, proc in procs.items():
            out, err = proc.communicate(timeout=600)
            lines = [l for l in out.splitlines() if l.startswith("PROBE_RESULT=")]
            cfg = json.loads(lines[-1][len("PROBE_RESULT="):]) if lines else None
            _results_cache[label] = (proc.returncode, cfg, err[-2000:])
    return _results_cache[case]


def _baseline():
    code, cfg, err = _probe("unset")
    assert code == 0 and cfg is not None, f"baseline import failed: {err}"
    return cfg


# --- Criterion 2: a stale non-numeric LOOPBACK_TIMEOUT is ignored ---------------

def test_c2_bridge_manager_imports_with_non_numeric_loopback_timeout():
    code, cfg, err = _probe("stale_non_numeric")
    assert code == 0, f"import of src.bridge.manager failed with LOOPBACK_TIMEOUT=abc:\n{err}"
    assert cfg is not None


def test_c2_non_numeric_loopback_timeout_leaves_client_config_unchanged():
    code, cfg, err = _probe("stale_non_numeric")
    assert code == 0, err
    assert cfg == _baseline()


def test_c2_numeric_loopback_timeout_does_not_change_loopback_timeout_ms():
    # No mapping of the old value into the add-in timeout (design decision 2).
    code, cfg, err = _probe("stale_numeric")
    assert code == 0, err
    assert cfg["loopback_timeout_ms"] == DEFAULT_TIMEOUT_MS


# --- Criterion 3: VSTO_LOOPBACK_TIMEOUT_MS row describes the applied timeout ---

def _vsto_table_rows():
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"^### VSTO / Outlook Bridge\s*$(.*?)(?=^#{2,3} |\Z)", text, re.M | re.S)
    assert match, "Table '### VSTO / Outlook Bridge' not found in doc/ENV_VARIABLES.md"
    rows = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells:
            rows[cells[0].strip("`* ")] = cells
    return rows


def test_c3_vsto_loopback_timeout_ms_row_keeps_default():
    row = _vsto_table_rows().get("VSTO_LOOPBACK_TIMEOUT_MS")
    assert row is not None, "VSTO_LOOPBACK_TIMEOUT_MS row missing"
    assert any(c.strip("` ") == str(DEFAULT_TIMEOUT_MS) for c in row[1:]), row


def test_c3_vsto_loopback_timeout_ms_row_says_it_applies_to_a_loopback_email():
    row = _vsto_table_rows().get("VSTO_LOOPBACK_TIMEOUT_MS")
    assert row is not None, "VSTO_LOOPBACK_TIMEOUT_MS row missing"
    description = row[-1].lower()
    # Loose wording check: the row must say it is the timeout applied to one
    # loopback email (not just "loopback timeout in milliseconds").
    assert "timeout" in description
    assert re.search(r"loopback e-?mail", description), description
    assert re.search(r"\bappl(y|ies|ied)\b", description), description


def test_c3_no_loopback_timeout_row_in_vsto_table():
    assert "LOOPBACK_TIMEOUT" not in _vsto_table_rows()


# --- Criterion 4: loopback_timeout_ms unchanged (no behaviour change) ----------

def test_c4_loopback_timeout_ms_default_when_vsto_variable_unset():
    assert _baseline()["loopback_timeout_ms"] == DEFAULT_TIMEOUT_MS


def test_c4_loopback_timeout_ms_follows_vsto_variable_when_set():
    code, cfg, err = _probe("vsto_set")
    assert code == 0, err
    assert cfg["loopback_timeout_ms"] == int(SYNTHETIC_TIMEOUT_MS)
