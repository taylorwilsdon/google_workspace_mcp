"""Service-account mode must not request a scope that cannot work under DWD."""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth.scopes import DRIVE_FILE_SCOPE, DRIVE_SCOPE, DRIVE_READONLY_SCOPE
from auth.service_decorator import _widen_drive_scope_for_dwd


def test_drive_file_is_widened_to_full_drive():
    assert _widen_drive_scope_for_dwd([DRIVE_FILE_SCOPE], "copy_drive_file") == [
        DRIVE_SCOPE
    ]


def test_other_scopes_are_left_alone():
    scopes = [DRIVE_READONLY_SCOPE, "https://www.googleapis.com/auth/spreadsheets"]
    assert _widen_drive_scope_for_dwd(scopes, "read_sheet_values") == scopes


def test_widening_preserves_companion_scopes_and_order():
    scopes = ["https://www.googleapis.com/auth/documents", DRIVE_FILE_SCOPE]
    assert _widen_drive_scope_for_dwd(scopes, "import_to_google_doc") == [
        "https://www.googleapis.com/auth/documents",
        DRIVE_SCOPE,
    ]
