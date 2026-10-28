import pytest

from gdrive.drive_tools import _paged_content_cache


@pytest.fixture(autouse=True)
def _empty_paged_content_cache():
    """get_drive_file_content keeps paged text in a module-level cache; start
    and end every Drive test with it empty so tests cannot read each other's."""
    _paged_content_cache.clear()
    yield
    _paged_content_cache.clear()
