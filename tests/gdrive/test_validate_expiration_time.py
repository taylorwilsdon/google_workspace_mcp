"""validate_expiration_time accepts only a complete RFC 3339 timestamp."""

import pytest

from gdrive.drive_helpers import validate_expiration_time


@pytest.mark.parametrize(
    "value",
    [
        "2025-01-15T00:00:00Z",
        "2025-01-15T00:00:00.5+02:00",
        "2025-01-15T00:00:00-05:00",
    ],
)
def test_accepts_rfc3339_timestamps(value):
    validate_expiration_time(value)


@pytest.mark.parametrize(
    "value",
    ["2025-01-15", "2025-01-15T00:00:00", "2025-01-15T00:00:00Z\n", ""],
)
def test_rejects_everything_else(value):
    with pytest.raises(ValueError, match="Invalid expiration_time"):
        validate_expiration_time(value)
