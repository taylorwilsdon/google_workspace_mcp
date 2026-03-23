"""
Tests for suggestions_view_mode support.

Covers the VALID_SUGGESTIONS_VIEW_MODES constant and validates
that invalid modes are correctly rejected.
"""

from gdocs.docs_helpers import VALID_SUGGESTIONS_VIEW_MODES


class TestValidSuggestionsViewModes:
    """Verify the constant contains the expected API values."""

    def test_contains_default_for_current_access(self):
        assert "DEFAULT_FOR_CURRENT_ACCESS" in VALID_SUGGESTIONS_VIEW_MODES

    def test_contains_suggestions_inline(self):
        assert "SUGGESTIONS_INLINE" in VALID_SUGGESTIONS_VIEW_MODES

    def test_contains_preview_suggestions_accepted(self):
        assert "PREVIEW_SUGGESTIONS_ACCEPTED" in VALID_SUGGESTIONS_VIEW_MODES

    def test_contains_preview_without_suggestions(self):
        assert "PREVIEW_WITHOUT_SUGGESTIONS" in VALID_SUGGESTIONS_VIEW_MODES

    def test_exactly_four_modes(self):
        assert len(VALID_SUGGESTIONS_VIEW_MODES) == 4

    def test_invalid_mode_not_in_set(self):
        assert "INVALID_MODE" not in VALID_SUGGESTIONS_VIEW_MODES
        assert "" not in VALID_SUGGESTIONS_VIEW_MODES
        assert "suggestions_inline" not in VALID_SUGGESTIONS_VIEW_MODES  # case sensitive
