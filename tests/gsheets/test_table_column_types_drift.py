"""
Guard test pinning TABLE_COLUMN_TYPES to the Sheets discovery document.

TABLE_COLUMN_TYPES is an allowlist used to reject a bad columnType with an
actionable message instead of an opaque HTTP 400 from the API. That is only
useful while the list matches the API.

The two drift directions are deliberately treated differently:

  * A value we allow that the API does not have is a real bug: we would send
    it and the user would get the opaque 400 the allowlist exists to prevent.
    That fails the test.

  * A value the API has that we do not list is a missing feature, not a bug.
    Failing on it would break routine dependency bumps, so it only warns.
"""

import json
import os
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gsheets.sheets_helpers import TABLE_COLUMN_TYPES

UNSPECIFIED = "COLUMN_TYPE_UNSPECIFIED"


def _api_column_types() -> set[str]:
    """ColumnType enum from the bundled discovery doc, minus the sentinel."""
    try:
        import googleapiclient
    except ImportError:  # pragma: no cover - dependency is required at runtime
        pytest.skip("google-api-python-client is not installed")

    doc = (
        Path(googleapiclient.__file__).parent
        / "discovery_cache"
        / "documents"
        / "sheets.v4.json"
    )
    if not doc.is_file():
        pytest.skip("bundled sheets.v4 discovery document not available")

    schemas = json.loads(doc.read_text())["schemas"]
    enum = schemas["TableColumnProperties"]["properties"]["columnType"]["enum"]
    return set(enum) - {UNSPECIFIED}


def test_no_column_type_we_allow_is_rejected_by_the_api():
    """Hard failure: an entry the API does not recognise defeats the allowlist."""
    stale = TABLE_COLUMN_TYPES - _api_column_types()

    assert not stale, (
        f"TABLE_COLUMN_TYPES contains values the Sheets API does not define: "
        f"{sorted(stale)}. Remove them, or they will reach the API as a 400."
    )


def test_unspecified_sentinel_is_not_offered():
    """The API documents COLUMN_TYPE_UNSPECIFIED as 'do not use'."""
    assert UNSPECIFIED not in TABLE_COLUMN_TYPES


def test_reports_column_types_the_api_added():
    """
    Soft signal: new API values are an opportunity, not a defect. Warn so a
    dependency bump surfaces them without failing CI.
    """
    missing = _api_column_types() - TABLE_COLUMN_TYPES

    if missing:
        warnings.warn(
            f"Sheets API defines column types not offered by manage_sheet_table: "
            f"{sorted(missing)}. Consider adding them to TABLE_COLUMN_TYPES.",
            stacklevel=2,
        )
