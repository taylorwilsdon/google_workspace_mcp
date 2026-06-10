"""Shared helpers for golden-file (schema snapshot) tests.

A golden test regenerates some output (e.g. a tool's JSON schema) and compares
it against a committed snapshot file, failing on any drift. This catches
*accidental* changes to a tool's public contract.

When a schema change is *intentional*, the snapshot must be refreshed. Set the
``UPDATE_GOLDEN=1`` environment variable to rewrite the golden files instead of
asserting against them, then review the resulting diff in git before committing::

    UPDATE_GOLDEN=1 uv run pytest tests/gcontacts/test_contacts_tools_v2.py

Default runs (without the variable) only compare and fail on drift.
"""

import json
import os
from difflib import unified_diff
from pathlib import Path
from typing import Any

import pytest

#: When this env var is "1", golden tests rewrite their snapshot instead of asserting.
UPDATE_GOLDEN = os.environ.get("UPDATE_GOLDEN") == "1"


def _serialize(data: Any) -> str:
    """Serialize ``data`` in the canonical golden-file format (stable across runs)."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def assert_matches_golden(generated: Any, golden_path: Path, label: str) -> None:
    """Compare ``generated`` against the golden file at ``golden_path``.

    In ``UPDATE_GOLDEN=1`` mode the golden file is rewritten with ``generated``
    and the function returns without asserting. Otherwise the parsed golden is
    compared against ``generated`` and a unified diff is raised via
    ``pytest.fail`` on any mismatch. ``label`` names the schema in the failure
    message (e.g. "Contacts").
    """
    if UPDATE_GOLDEN:
        golden_path.write_text(_serialize(generated))
        return

    golden = json.loads(golden_path.read_text())
    if generated != golden:
        expected = json.dumps(golden, indent=2, sort_keys=True).splitlines()
        actual = json.dumps(generated, indent=2, sort_keys=True).splitlines()
        diff = "\n".join(
            unified_diff(
                expected,
                actual,
                fromfile=str(golden_path),
                tofile="generated",
                lineterm="",
            )
        )
        pytest.fail(
            f"{label} tool schema drifted from golden:\n{diff}\n\n"
            "If this change is intentional, regenerate the snapshot with:\n"
            "    UPDATE_GOLDEN=1 uv run pytest"
        )
