"""Shared helpers for creating disk-backed key-value stores."""

import string

from key_value.aio._utils.sanitization import HybridSanitizationStrategy
from key_value.aio.stores.filetree import FileTreeStore

SAFE_FILENAME_CHARS = string.ascii_letters + string.digits + "-_."
"""Characters allowed in on-disk file names for key-value stores."""


def make_sanitized_file_store(data_directory: str) -> FileTreeStore:
    """Return a ``FileTreeStore`` using the project-wide sanitization rules.

    Both the OAuth-proxy server storage and the CLI token storage need
    identical sanitization; this factory keeps them in sync.
    """
    return FileTreeStore(
        data_directory=data_directory,
        key_sanitization_strategy=HybridSanitizationStrategy(
            allowed_characters=SAFE_FILENAME_CHARS,
        ),
    )
