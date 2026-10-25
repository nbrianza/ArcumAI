# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Issue #39, owner decision on PR #67: safe_ext is an allow-list.

Only known extensions (the formats the readers and the loopback processor handle,
plus common office, image and archive formats) may appear in default logs. Anything
else, including suffixes with trailing whitespace or a line break, becomes the fixed
placeholder "other". The add-in SafeExt uses the same list (static check: the C#
add-in cannot run here; runtime evidence is the CI msbuild step).
"""
import re
from pathlib import Path

import pytest

from src.logger import SAFE_EXTENSIONS, safe_ext

MARKER = "ZZ-SYNTHETIC-MARKER-123"
EXTRACTOR = (Path(__file__).resolve().parent.parent / "outlook-plugin" / "ArcumAI.Outlook"
             / "ArcumAI.OutlookAddIn" / "Core" / "Loopback" / "AttachmentExtractor.cs")


@pytest.mark.parametrize("ext", [
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".msg", ".eml", ".txt", ".csv", ".md", ".pptx",
])
def test_known_extensions_are_logged(ext):
    assert safe_ext(f"{MARKER}{ext}") == ext
    assert safe_ext(f"{MARKER}{ext.upper()}") == ext


@pytest.mark.parametrize("name", [
    f"{MARKER}.pdf ",            # trailing space
    f"{MARKER}.pdf\t",           # trailing tab
    f"{MARKER}.pdf\n",           # trailing line break
    f"{MARKER}.pd\nf",           # line break inside the suffix
    f"{MARKER}.pdf.",            # trailing dot
    f"{MARKER}.abcdefgh",        # short alphanumeric but unknown
    f"{MARKER}.zz1",
    f"report.{MARKER}",
    f"{MARKER}",
    "",
    None,
])
def test_unknown_or_malformed_extensions_become_placeholder(name):
    assert safe_ext(name) == "other"


def test_placeholder_never_carries_input():
    for name in (f"a.{MARKER}", f"{MARKER}.exe", f"x.{MARKER[:8].lower()}"):
        assert MARKER.lower()[:8] not in safe_ext(name).lower()


def test_allow_list_entries_are_plain_lower_case_extensions():
    assert SAFE_EXTENSIONS
    assert all(re.fullmatch(r"\.[a-z0-9]{1,8}", ext) for ext in SAFE_EXTENSIONS)


def test_addin_safe_ext_uses_the_same_allow_list():
    source = EXTRACTOR.read_text(encoding="utf-8-sig")
    m = re.search(r"HashSet<string>\s+SafeExtensions\s*=\s*new\s+HashSet<string>\([^)]*\)\s*\{(.*?)\};", source, re.S)
    assert m, "AttachmentExtractor.SafeExtensions allow-list not found"
    addin_list = set(re.findall(r'"(\.[a-z0-9]+)"', m.group(1)))
    assert addin_list == set(SAFE_EXTENSIONS), "server and add-in allow-lists differ"
    body = source[source.index("private static string SafeExt("):]
    body = body[:body.index("\n        }") + 10]
    assert "SafeExtensions.Contains(" in body and '"other"' in body
