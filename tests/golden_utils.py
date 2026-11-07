"""Golden-file assertions for tool schema snapshot tests."""

import json
import os
from difflib import unified_diff
from pathlib import Path
from typing import Any

import pytest

UPDATE_GOLDEN = os.environ.get("UPDATE_GOLDEN") == "1"


def assert_matches_golden(generated: Any, golden_path: Path, label: str) -> None:
    """Fail with a diff if ``generated`` drifts from the canonical golden JSON.

    With ``UPDATE_GOLDEN=1`` the golden file is rewritten instead.
    """
    actual = json.dumps(generated, indent=2) + "\n"
    if UPDATE_GOLDEN:
        golden_path.write_text(actual, encoding="utf-8")
        return

    expected_lines = golden_path.read_text(encoding="utf-8").splitlines()
    actual_lines = actual.splitlines()
    if actual_lines != expected_lines:
        diff = "\n".join(
            unified_diff(
                expected_lines,
                actual_lines,
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
