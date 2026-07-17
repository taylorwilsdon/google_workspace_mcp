"""Shared Google Docs index and document helpers."""


def utf16_length(value: str) -> int:
    """Return the number of UTF-16 code units used by Google Docs indices."""
    return len(value.encode("utf-16-le")) // 2
