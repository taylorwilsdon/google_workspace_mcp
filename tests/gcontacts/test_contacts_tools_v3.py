"""
Unit tests for nicknames, urls, userDefined, relations write fields
added on top of PR #688's multi-phone/email/org pattern.

Covers:
- _coerce_* input helpers for the 4 new fields
- _normalize_* helpers (url trailing-slash strip, nickname/key/person lowercasing)
- _format_contact rendering for nicknames (always shown), urls/userDefined/relations (detailed)
- _build_person_body integration of the 4 new params
- _merge_nicknames / _merge_urls / _merge_user_defined / _merge_relations
- Empty-notes-clear bug fix (notes="" now clears the bio field)
"""

from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcontacts.contacts_tools import (  # noqa: E402
    NicknameInput,
    RelationInput,
    UrlInput,
    UserDefinedInput,
    _build_person_body,
    _coerce_nickname_input,
    _coerce_relation_input,
    _coerce_url_input,
    _coerce_user_defined_input,
    _format_contact,
    _merge_nicknames,
    _merge_relations,
    _merge_urls,
    _merge_user_defined,
    _normalize_nickname,
    _normalize_relation_person,
    _normalize_url,
    _normalize_user_defined_key,
)


# =============================================================================
# _coerce_*_input — accept Pydantic instance, plain string (where supported),
# or dict (Pydantic-validated)
# =============================================================================


class TestCoerceNicknameInput:
    def test_from_string(self):
        result = _coerce_nickname_input("Bob")
        assert isinstance(result, NicknameInput)
        assert result.value == "Bob"
        assert result.type is None

    def test_from_dict(self):
        result = _coerce_nickname_input({"value": "Bob", "type": "alternate_name"})
        assert isinstance(result, NicknameInput)
        assert result.value == "Bob"
        assert result.type == "alternate_name"

    def test_from_instance_passthrough(self):
        original = NicknameInput(value="Bob")
        assert _coerce_nickname_input(original) is original

    def test_dict_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            _coerce_nickname_input({"value": "Bob", "unknown_field": "x"})


class TestCoerceUrlInput:
    def test_from_string(self):
        result = _coerce_url_input("https://example.com")
        assert isinstance(result, UrlInput)
        assert result.value == "https://example.com"
        assert result.type is None

    def test_from_dict_with_type(self):
        result = _coerce_url_input({"value": "https://example.com", "type": "homepage"})
        assert result.value == "https://example.com"
        assert result.type == "homepage"

    def test_from_instance_passthrough(self):
        original = UrlInput(value="https://example.com")
        assert _coerce_url_input(original) is original


class TestCoerceUserDefinedInput:
    def test_from_dict(self):
        result = _coerce_user_defined_input({"key": "Account ID", "value": "12345"})
        assert isinstance(result, UserDefinedInput)
        assert result.key == "Account ID"
        assert result.value == "12345"

    def test_from_instance_passthrough(self):
        original = UserDefinedInput(key="K", value="V")
        assert _coerce_user_defined_input(original) is original

    def test_key_only_accepted_for_remove(self):
        """value defaults to '' so key-only dicts work for remove mode."""
        result = _coerce_user_defined_input({"key": "Only Key"})
        assert isinstance(result, UserDefinedInput)
        assert result.key == "Only Key"
        assert result.value == ""

    def test_requires_key(self):
        with pytest.raises(ValidationError):
            _coerce_user_defined_input({"value": "Only Value"})


class TestCoerceRelationInput:
    def test_from_string(self):
        result = _coerce_relation_input("Jane Doe")
        assert isinstance(result, RelationInput)
        assert result.person == "Jane Doe"
        assert result.type is None

    def test_from_dict(self):
        result = _coerce_relation_input({"person": "Jane Doe", "type": "spouse"})
        assert result.person == "Jane Doe"
        assert result.type == "spouse"

    def test_from_instance_passthrough(self):
        original = RelationInput(person="Jane Doe", type="spouse")
        assert _coerce_relation_input(original) is original


# =============================================================================
# _normalize_* helpers — used as dedup keys
# =============================================================================


class TestNormalizeUrl:
    def test_lowercase(self):
        assert _normalize_url("HTTPS://Example.COM/path") == "https://example.com/path"

    def test_strip_trailing_slash(self):
        assert _normalize_url("https://example.com/") == "https://example.com"

    def test_strip_whitespace(self):
        assert _normalize_url("  https://example.com  ") == "https://example.com"

    def test_no_trailing_slash_unchanged(self):
        assert _normalize_url("https://example.com/path") == "https://example.com/path"

    def test_only_trailing_slash_stripped_not_internal(self):
        assert (
            _normalize_url("https://example.com/path/sub")
            == "https://example.com/path/sub"
        )


class TestNormalizeNickname:
    def test_lowercase(self):
        assert _normalize_nickname("Bob") == "bob"

    def test_strip_whitespace(self):
        assert _normalize_nickname("  Bob  ") == "bob"


class TestNormalizeUserDefinedKey:
    def test_lowercase_and_strip(self):
        assert _normalize_user_defined_key("  Account ID  ") == "account id"


class TestNormalizeRelationPerson:
    def test_lowercase_and_strip(self):
        assert _normalize_relation_person("  Jane Doe  ") == "jane doe"


# =============================================================================
# _format_contact — rendering for the 4 new fields
# =============================================================================


class TestFormatContactNicknames:
    """Nicknames are shown in the default (non-detailed) view."""

    def test_single_nickname_shown(self):
        person = {
            "resourceName": "people/c1",
            "nicknames": [{"value": "Bobby"}],
        }
        result = _format_contact(person)
        assert "Nicknames: Bobby" in result

    def test_multiple_nicknames_comma_joined(self):
        person = {
            "resourceName": "people/c1",
            "nicknames": [{"value": "Bobby"}, {"value": "B"}],
        }
        result = _format_contact(person)
        assert "Nicknames: Bobby, B" in result

    def test_empty_value_filtered(self):
        person = {
            "resourceName": "people/c1",
            "nicknames": [{"value": ""}, {"value": "Bobby"}],
        }
        result = _format_contact(person)
        assert "Nicknames: Bobby" in result
        assert "Nicknames: , Bobby" not in result

    def test_no_nicknames_no_line(self):
        person = {"resourceName": "people/c1"}
        result = _format_contact(person)
        assert "Nicknames" not in result


class TestFormatContactUrls:
    """URLs are shown only in the detailed view."""

    def test_urls_not_in_default_view(self):
        person = {
            "resourceName": "people/c1",
            "urls": [{"value": "https://example.com"}],
        }
        result = _format_contact(person, detailed=False)
        assert "URLs" not in result

    def test_single_url_shown_in_detailed(self):
        person = {
            "resourceName": "people/c1",
            "urls": [{"value": "https://example.com"}],
        }
        result = _format_contact(person, detailed=True)
        assert "URLs: https://example.com" in result

    def test_multiple_urls_comma_joined(self):
        person = {
            "resourceName": "people/c1",
            "urls": [
                {"value": "https://example.com"},
                {"value": "https://github.com/user"},
            ],
        }
        result = _format_contact(person, detailed=True)
        assert "URLs: https://example.com, https://github.com/user" in result


class TestFormatContactUserDefined:
    """userDefined custom fields shown only in detailed view, as key:value lines."""

    def test_user_defined_not_in_default(self):
        person = {
            "resourceName": "people/c1",
            "userDefined": [{"key": "Account ID", "value": "12345"}],
        }
        result = _format_contact(person, detailed=False)
        assert "Custom Fields" not in result

    def test_user_defined_shown_in_detailed(self):
        person = {
            "resourceName": "people/c1",
            "userDefined": [{"key": "Account ID", "value": "12345"}],
        }
        result = _format_contact(person, detailed=True)
        assert "Custom Fields:" in result
        assert "  - Account ID: 12345" in result

    def test_multiple_user_defined_each_on_own_line(self):
        person = {
            "resourceName": "people/c1",
            "userDefined": [
                {"key": "Account ID", "value": "12345"},
                {"key": "Internal Code", "value": "X-7"},
            ],
        }
        result = _format_contact(person, detailed=True)
        assert "  - Account ID: 12345" in result
        assert "  - Internal Code: X-7" in result

    def test_empty_key_or_value_filtered(self):
        person = {
            "resourceName": "people/c1",
            "userDefined": [
                {"key": "", "value": "v"},
                {"key": "k", "value": ""},
                {"key": "ok", "value": "yes"},
            ],
        }
        result = _format_contact(person, detailed=True)
        assert "  - ok: yes" in result
        # The two empty entries shouldn't render
        lines = result.split("Custom Fields:")[1].splitlines()
        custom_field_lines = [line for line in lines if line.strip()]
        assert not any(": v" in line for line in custom_field_lines)
        assert not any(line.startswith("  - k:") for line in custom_field_lines)


class TestFormatContactRelations:
    """Relations shown only in detailed view, with type when present."""

    def test_relations_not_in_default(self):
        person = {
            "resourceName": "people/c1",
            "relations": [{"person": "Jane Doe", "type": "spouse"}],
        }
        result = _format_contact(person, detailed=False)
        assert "Relations" not in result

    def test_relation_with_type_shown(self):
        person = {
            "resourceName": "people/c1",
            "relations": [{"person": "Jane Doe", "type": "spouse"}],
        }
        result = _format_contact(person, detailed=True)
        assert "Relations:" in result
        assert "  - Jane Doe (spouse)" in result

    def test_relation_with_formatted_type_preferred(self):
        person = {
            "resourceName": "people/c1",
            "relations": [
                {"person": "Jane Doe", "type": "spouse", "formattedType": "Spouse"}
            ],
        }
        result = _format_contact(person, detailed=True)
        assert "  - Jane Doe (Spouse)" in result

    def test_relation_without_type(self):
        person = {
            "resourceName": "people/c1",
            "relations": [{"person": "Jane Doe"}],
        }
        result = _format_contact(person, detailed=True)
        assert "  - Jane Doe" in result
        assert "  - Jane Doe (" not in result

    def test_empty_person_filtered(self):
        person = {
            "resourceName": "people/c1",
            "relations": [{"person": ""}, {"person": "Jane Doe", "type": "spouse"}],
        }
        result = _format_contact(person, detailed=True)
        assert "  - Jane Doe (spouse)" in result


# =============================================================================
# _build_person_body — integration of the 4 new params
# =============================================================================


class TestBuildPersonBodyNewFields:
    def test_nicknames_built(self):
        body = _build_person_body(
            nicknames=[NicknameInput(value="Bobby"), NicknameInput(value="B")]
        )
        assert body["nicknames"] == [{"value": "Bobby"}, {"value": "B"}]

    def test_nickname_with_type_built(self):
        body = _build_person_body(
            nicknames=[NicknameInput(value="Bobby", type="alternate_name")]
        )
        assert body["nicknames"] == [{"value": "Bobby", "type": "alternate_name"}]

    def test_urls_built(self):
        body = _build_person_body(
            urls=[UrlInput(value="https://example.com", type="homepage")]
        )
        assert body["urls"] == [{"value": "https://example.com", "type": "homepage"}]

    def test_user_defined_built(self):
        body = _build_person_body(
            user_defined=[UserDefinedInput(key="Account ID", value="12345")]
        )
        assert body["userDefined"] == [{"key": "Account ID", "value": "12345"}]

    def test_relations_built(self):
        body = _build_person_body(
            relations=[RelationInput(person="Jane Doe", type="spouse")]
        )
        assert body["relations"] == [{"person": "Jane Doe", "type": "spouse"}]

    def test_relation_without_type(self):
        body = _build_person_body(relations=[RelationInput(person="Jane Doe")])
        assert body["relations"] == [{"person": "Jane Doe"}]

    def test_all_four_new_fields_together(self):
        body = _build_person_body(
            given_name="Test",
            family_name="User",
            nicknames=[NicknameInput(value="T")],
            urls=[UrlInput(value="https://example.com")],
            user_defined=[UserDefinedInput(key="K", value="V")],
            relations=[RelationInput(person="Spouse Person", type="spouse")],
        )
        assert body["names"][0]["givenName"] == "Test"
        assert body["nicknames"] == [{"value": "T"}]
        assert body["urls"] == [{"value": "https://example.com"}]
        assert body["userDefined"] == [{"key": "K", "value": "V"}]
        assert body["relations"] == [{"person": "Spouse Person", "type": "spouse"}]

    def test_omitted_fields_not_in_body(self):
        body = _build_person_body(given_name="Only Name")
        assert "nicknames" not in body
        assert "urls" not in body
        assert "userDefined" not in body
        assert "relations" not in body


# =============================================================================
# _merge_nicknames
# =============================================================================


class TestMergeNicknames:
    def test_merge_adds_new(self):
        existing = [{"value": "Bobby"}]
        new = [{"value": "B"}]
        result = _merge_nicknames(existing, new, "merge")
        assert len(result) == 2

    def test_merge_dedup_case_insensitive(self):
        existing = [{"value": "Bobby"}]
        new = [{"value": "BOBBY"}]
        result = _merge_nicknames(existing, new, "merge")
        assert len(result) == 1

    def test_merge_dedup_strips_whitespace(self):
        existing = [{"value": "Bobby"}]
        new = [{"value": "  bobby  "}]
        result = _merge_nicknames(existing, new, "merge")
        assert len(result) == 1

    def test_replace(self):
        existing = [{"value": "Old"}]
        new = [{"value": "New"}]
        result = _merge_nicknames(existing, new, "replace")
        assert result == [{"value": "New"}]

    def test_remove(self):
        existing = [{"value": "Keep"}, {"value": "Remove"}]
        result = _merge_nicknames(existing, [{"value": "remove"}], "remove")
        assert result == [{"value": "Keep"}]

    def test_merge_empty_existing(self):
        result = _merge_nicknames([], [{"value": "New"}], "merge")
        assert len(result) == 1

    def test_replace_with_empty_clears(self):
        existing = [{"value": "OldOne"}]
        result = _merge_nicknames(existing, [], "replace")
        assert result == []


# =============================================================================
# _merge_urls
# =============================================================================


class TestMergeUrls:
    def test_merge_adds_new(self):
        existing = [{"value": "https://example.com"}]
        new = [{"value": "https://other.com"}]
        result = _merge_urls(existing, new, "merge")
        assert len(result) == 2

    def test_merge_dedup_case_insensitive(self):
        existing = [{"value": "HTTPS://Example.COM"}]
        new = [{"value": "https://example.com"}]
        result = _merge_urls(existing, new, "merge")
        assert len(result) == 1

    def test_merge_dedup_trailing_slash(self):
        existing = [{"value": "https://example.com"}]
        new = [{"value": "https://example.com/"}]
        result = _merge_urls(existing, new, "merge")
        assert len(result) == 1

    def test_merge_keeps_distinct_paths(self):
        existing = [{"value": "https://example.com/a"}]
        new = [{"value": "https://example.com/b"}]
        result = _merge_urls(existing, new, "merge")
        assert len(result) == 2

    def test_replace(self):
        existing = [{"value": "https://old.com"}]
        new = [{"value": "https://new.com"}]
        result = _merge_urls(existing, new, "replace")
        assert result == [{"value": "https://new.com"}]

    def test_remove(self):
        existing = [
            {"value": "https://keep.com"},
            {"value": "https://remove.com"},
        ]
        result = _merge_urls(existing, [{"value": "https://remove.com"}], "remove")
        assert result == [{"value": "https://keep.com"}]

    def test_remove_normalizes_for_match(self):
        existing = [{"value": "https://example.com"}]
        # Stored without slash, request has slash + caps — must still match
        result = _merge_urls(existing, [{"value": "HTTPS://Example.COM/"}], "remove")
        assert result == []


# =============================================================================
# _merge_user_defined
# =============================================================================


class TestMergeUserDefined:
    def test_merge_adds_new(self):
        existing = [{"key": "K1", "value": "V1"}]
        new = [{"key": "K2", "value": "V2"}]
        result = _merge_user_defined(existing, new, "merge")
        assert len(result) == 2

    def test_merge_overrides_value_on_key_match(self):
        """The userDefined merge has special semantics: same key → new value wins."""
        existing = [{"key": "Account ID", "value": "OLD"}]
        new = [{"key": "Account ID", "value": "NEW"}]
        result = _merge_user_defined(existing, new, "merge")
        assert len(result) == 1
        assert result[0]["value"] == "NEW"

    def test_merge_key_match_case_insensitive(self):
        existing = [{"key": "Account ID", "value": "OLD"}]
        new = [{"key": "ACCOUNT ID", "value": "NEW"}]
        result = _merge_user_defined(existing, new, "merge")
        assert len(result) == 1
        assert result[0]["value"] == "NEW"

    def test_merge_preserves_unaffected_existing(self):
        existing = [
            {"key": "Untouched", "value": "X"},
            {"key": "Account ID", "value": "OLD"},
        ]
        new = [{"key": "Account ID", "value": "NEW"}]
        result = _merge_user_defined(existing, new, "merge")
        assert len(result) == 2
        keys = {ud["key"] for ud in result}
        assert keys == {"Untouched", "Account ID"}

    def test_replace(self):
        existing = [{"key": "K1", "value": "V1"}, {"key": "K2", "value": "V2"}]
        new = [{"key": "K3", "value": "V3"}]
        result = _merge_user_defined(existing, new, "replace")
        assert result == [{"key": "K3", "value": "V3"}]

    def test_remove_by_key(self):
        existing = [{"key": "Keep", "value": "Y"}, {"key": "Drop", "value": "N"}]
        result = _merge_user_defined(existing, [{"key": "Drop"}], "remove")
        assert result == [{"key": "Keep", "value": "Y"}]

    def test_remove_case_insensitive(self):
        existing = [{"key": "Drop", "value": "N"}]
        result = _merge_user_defined(existing, [{"key": "DROP"}], "remove")
        assert result == []


# =============================================================================
# _merge_relations
# =============================================================================


class TestMergeRelations:
    def test_merge_adds_new(self):
        existing = [{"person": "Jane Doe", "type": "spouse"}]
        new = [{"person": "John Doe", "type": "father"}]
        result = _merge_relations(existing, new, "merge")
        assert len(result) == 2

    def test_merge_dedup_by_person_and_type_tuple(self):
        existing = [{"person": "Jane Doe", "type": "spouse"}]
        new = [{"person": "jane doe", "type": "spouse"}]
        result = _merge_relations(existing, new, "merge")
        assert len(result) == 1

    def test_merge_keeps_same_person_with_different_type(self):
        """Same person, different relation type → both kept (you can be both 'spouse' and 'partner')."""
        existing = [{"person": "Jane Doe", "type": "spouse"}]
        new = [{"person": "Jane Doe", "type": "partner"}]
        result = _merge_relations(existing, new, "merge")
        assert len(result) == 2

    def test_merge_keeps_same_person_with_different_formatted_type(self):
        existing = [{"person": "Jane Doe", "type": "custom", "formattedType": "Aunt"}]
        new = [{"person": "Jane Doe", "type": "custom", "formattedType": "Mentor"}]
        result = _merge_relations(existing, new, "merge")
        assert len(result) == 2

    def test_merge_no_type_treated_as_distinct(self):
        existing = [{"person": "Jane Doe", "type": "spouse"}]
        new = [{"person": "Jane Doe"}]  # no type
        result = _merge_relations(existing, new, "merge")
        assert len(result) == 2

    def test_replace(self):
        existing = [{"person": "Old Spouse", "type": "spouse"}]
        new = [{"person": "New Spouse", "type": "spouse"}]
        result = _merge_relations(existing, new, "replace")
        assert result == [{"person": "New Spouse", "type": "spouse"}]

    def test_remove(self):
        existing = [
            {"person": "Keep", "type": "friend"},
            {"person": "Remove", "type": "spouse"},
        ]
        result = _merge_relations(
            existing, [{"person": "Remove", "type": "spouse"}], "remove"
        )
        assert result == [{"person": "Keep", "type": "friend"}]

    def test_remove_case_insensitive(self):
        existing = [{"person": "Jane Doe", "type": "spouse"}]
        result = _merge_relations(
            existing, [{"person": "JANE DOE", "type": "SPOUSE"}], "remove"
        )
        assert result == []

    def test_remove_uses_formatted_type(self):
        existing = [
            {"person": "Jane Doe", "type": "custom", "formattedType": "Aunt"},
            {"person": "Jane Doe", "type": "custom", "formattedType": "Mentor"},
        ]
        result = _merge_relations(
            existing,
            [{"person": "JANE DOE", "type": "custom", "formattedType": " aunt "}],
            "remove",
        )
        assert result == [
            {"person": "Jane Doe", "type": "custom", "formattedType": "Mentor"}
        ]


# =============================================================================
# Empty-notes-clear bug fix
# =============================================================================


class TestNotesClearBugFix:
    """Previously `if notes:` swallowed the empty string. Fixed to `if notes is not None:`
    so an explicit empty string clears the bio."""

    def test_notes_text_sets_bio(self):
        body = _build_person_body(notes="Important contact")
        assert body["biographies"] == [
            {"value": "Important contact", "contentType": "TEXT_PLAIN"}
        ]

    def test_notes_empty_string_clears_bio(self):
        """notes='' is the explicit signal to clear — must produce biographies=[]."""
        body = _build_person_body(notes="")
        assert body.get("biographies") == []

    def test_notes_none_does_not_touch_bio(self):
        """notes=None means no opinion, do not add the biographies key at all."""
        body = _build_person_body(given_name="Test")
        assert "biographies" not in body
