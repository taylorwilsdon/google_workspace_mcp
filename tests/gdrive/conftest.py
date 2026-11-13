import pytest

from gdrive.drive_tools import _paged_content_cache


@pytest.fixture(autouse=True)
def _empty_paged_content_cache():
    _paged_content_cache.clear()
    yield
    _paged_content_cache.clear()
