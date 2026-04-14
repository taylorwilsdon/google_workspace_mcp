"""
QA integration and edge-case tests for contacts v2 API.

Covers gaps identified in the coder's 65 unit tests:
- manage_contact action="update" with phones_mode="merge" (real read-modify-write flow)
- phones_mode="replace" vs "merge" vs "remove" behavioral difference
- Retry on 412 etag conflict
- manage_contacts_batch with field="phoneNumbers" (dict format, updateMask)
- manage_contacts_batch without field param (UserInputError)
- Deprecated aliases via manage_contact (backward compat + DeprecationWarning)
- Simultaneous deprecated phone + phones (phones wins)
- _format_contact for contact with 3 phones of different types
- _normalize_phone for internal short numbers (must not mutate)

All tests call the unwrapped async function directly to bypass auth decorators.
"""

import asyncio
import sys
import os
import warnings

import pytest
from unittest.mock import MagicMock
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcontacts.contacts_tools import (
    manage_contact as _manage_contact_wrapped,
    manage_contacts_batch as _manage_contacts_batch_wrapped,
    _format_contact,
    _normalize_phone,
)
from core.utils import UserInputError


def _unwrap(fn):
    """Strip all decorator wrappers to reach the raw async function."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


manage_contact = _unwrap(_manage_contact_wrapped)
manage_contacts_batch = _unwrap(_manage_contacts_batch_wrapped)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# =============================================================================
# Test 8: manage_contact action="update" phones_mode="merge"
# =============================================================================


class TestManageContactUpdateMerge:
    """Integration: update with phones_mode='merge' performs read-modify-write."""

    def _existing_contact(self):
        return {
            "resourceName": "people/c123",
            "etag": "E1",
            "phoneNumbers": [
                {"value": "+79270000000", "type": "mobile"},
                {"value": "+78482123456", "type": "work"},
            ],
        }

    def _update_result(self, phones):
        return {
            "resourceName": "people/c123",
            "etag": "E2",
            "phoneNumbers": phones,
        }

    def test_merge_adds_internal_to_existing_two_phones(self):
        """Adding internal 250 to contact with two phones results in three phones in API call."""
        new_phones = [
            {"value": "+79270000000", "type": "mobile"},
            {"value": "+78482123456", "type": "work"},
            {"value": "250", "type": "internal"},
        ]
        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.return_value = (
            self._existing_contact()
        )
        svc.people.return_value.updateContact.return_value.execute.return_value = (
            self._update_result(new_phones)
        )

        run(
            manage_contact(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                contact_id="c123",
                phones=[{"number": "250", "type": "internal"}],
                phones_mode="merge",
            )
        )

        update_kwargs = svc.people.return_value.updateContact.call_args.kwargs
        body = update_kwargs["body"]
        assert "phoneNumbers" in body
        assert len(body["phoneNumbers"]) == 3
        values = [p["value"] for p in body["phoneNumbers"]]
        assert "+79270000000" in values
        assert "+78482123456" in values
        assert "250" in values

    def test_merge_passes_updatePersonFields_phoneNumbers(self):
        """updatePersonFields must contain 'phoneNumbers' when phones updated."""
        new_phones = [
            {"value": "+79270000000", "type": "mobile"},
            {"value": "250", "type": "internal"},
        ]
        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.return_value = (
            self._existing_contact()
        )
        svc.people.return_value.updateContact.return_value.execute.return_value = (
            self._update_result(new_phones)
        )

        run(
            manage_contact(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                contact_id="c123",
                phones=[{"number": "250", "type": "internal"}],
                phones_mode="merge",
            )
        )

        update_kwargs = svc.people.return_value.updateContact.call_args.kwargs
        assert "phoneNumbers" in update_kwargs["updatePersonFields"]

    def test_merge_etag_from_current_contact_used_in_body(self):
        """etag from GET response must appear in the updateContact body."""
        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.return_value = (
            self._existing_contact()
        )
        svc.people.return_value.updateContact.return_value.execute.return_value = (
            self._update_result([{"value": "250", "type": "internal"}])
        )

        run(
            manage_contact(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                contact_id="c123",
                phones=[{"number": "250", "type": "internal"}],
            )
        )

        update_kwargs = svc.people.return_value.updateContact.call_args.kwargs
        assert update_kwargs["body"]["etag"] == "E1"


# =============================================================================
# Test 9: phones_mode="replace" vs "merge" vs "remove" behavioural difference
# =============================================================================


class TestPhonesModesBehaviorDifference:
    """Verify three modes produce distinctly different outcomes."""

    def _existing(self):
        return {
            "resourceName": "people/c123",
            "etag": "E1",
            "phoneNumbers": [
                {"value": "+79270000000", "type": "mobile"},
                {"value": "+78482123456", "type": "work"},
            ],
        }

    def _noop_result(self):
        return {"resourceName": "people/c123", "etag": "E2", "phoneNumbers": []}

    def _run_update(self, svc, mode):
        run(
            manage_contact(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                contact_id="c123",
                phones=[{"number": "+79991112233", "type": "other"}],
                phones_mode=mode,
            )
        )
        return svc.people.return_value.updateContact.call_args.kwargs["body"][
            "phoneNumbers"
        ]

    def test_replace_leaves_only_new_phone(self):
        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.return_value = self._existing()
        svc.people.return_value.updateContact.return_value.execute.return_value = (
            self._noop_result()
        )
        result_phones = self._run_update(svc, "replace")
        assert len(result_phones) == 1
        assert result_phones[0]["value"] == "+79991112233"

    def test_merge_adds_new_phone_keeping_existing(self):
        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.return_value = self._existing()
        svc.people.return_value.updateContact.return_value.execute.return_value = (
            self._noop_result()
        )
        result_phones = self._run_update(svc, "merge")
        values = [p["value"] for p in result_phones]
        assert len(result_phones) == 3
        assert "+79270000000" in values
        assert "+78482123456" in values
        assert "+79991112233" in values

    def test_remove_deletes_matching_phone_keeps_other(self):
        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.return_value = self._existing()
        svc.people.return_value.updateContact.return_value.execute.return_value = (
            self._noop_result()
        )
        run(
            manage_contact(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                contact_id="c123",
                phones=[{"number": "+79270000000", "type": "mobile"}],
                phones_mode="remove",
            )
        )
        result_phones = svc.people.return_value.updateContact.call_args.kwargs["body"][
            "phoneNumbers"
        ]
        values = [p["value"] for p in result_phones]
        assert "+79270000000" not in values
        assert "+78482123456" in values


# =============================================================================
# Test 10: retry on 412 etag conflict
# =============================================================================


class TestEtagRetryOn412:
    """manage_contact retries update on 412 Precondition Failed and succeeds on second attempt."""

    def test_retry_succeeds_on_second_attempt(self):
        resp_412 = MagicMock()
        resp_412.status = 412
        http_412 = HttpError(resp=resp_412, content=b"Precondition Failed")

        first_contact = {
            "resourceName": "people/c123",
            "etag": "E_STALE",
            "phoneNumbers": [],
        }
        second_contact = {
            "resourceName": "people/c123",
            "etag": "E_FRESH",
            "phoneNumbers": [],
        }
        update_success = {
            "resourceName": "people/c123",
            "etag": "E_AFTER",
            "phoneNumbers": [{"value": "+79270000000", "type": "mobile"}],
        }

        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.side_effect = [
            first_contact,
            second_contact,
        ]
        svc.people.return_value.updateContact.return_value.execute.side_effect = [
            http_412,
            update_success,
        ]

        result = run(
            manage_contact(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                contact_id="c123",
                phones=[{"number": "+79270000000", "type": "mobile"}],
                phones_mode="replace",
            )
        )

        assert svc.people.return_value.updateContact.call_count == 2
        assert "Contact Updated" in result

    def test_retry_uses_fresh_etag_on_second_attempt(self):
        """After 412, the second GET's etag must be used in the second updateContact call."""
        resp_412 = MagicMock()
        resp_412.status = 412
        http_412 = HttpError(resp=resp_412, content=b"Precondition Failed")

        first_contact = {
            "resourceName": "people/c123",
            "etag": "STALE",
            "phoneNumbers": [],
        }
        second_contact = {
            "resourceName": "people/c123",
            "etag": "FRESH",
            "phoneNumbers": [],
        }
        update_success = {
            "resourceName": "people/c123",
            "etag": "AFTER",
            "phoneNumbers": [{"value": "+79270000000", "type": "mobile"}],
        }

        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.side_effect = [
            first_contact,
            second_contact,
        ]
        svc.people.return_value.updateContact.return_value.execute.side_effect = [
            http_412,
            update_success,
        ]

        run(
            manage_contact(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                contact_id="c123",
                phones=[{"number": "+79270000000", "type": "mobile"}],
                phones_mode="replace",
            )
        )

        second_call_body = svc.people.return_value.updateContact.call_args_list[
            1
        ].kwargs["body"]
        assert second_call_body["etag"] == "FRESH"

    def test_retry_exhausted_reraises_412(self):
        """If all 3 retries fail with 412, the error propagates."""
        resp_412 = MagicMock()
        resp_412.status = 412
        http_412 = HttpError(resp=resp_412, content=b"Precondition Failed")

        contact = {"resourceName": "people/c123", "etag": "E1", "phoneNumbers": []}
        svc = MagicMock()
        svc.people.return_value.get.return_value.execute.return_value = contact
        svc.people.return_value.updateContact.return_value.execute.side_effect = (
            http_412
        )

        with pytest.raises(HttpError) as exc_info:
            run(
                manage_contact(
                    service=svc,
                    user_google_email="test@example.com",
                    action="update",
                    contact_id="c123",
                    phones=[{"number": "+79270000000", "type": "mobile"}],
                    phones_mode="replace",
                )
            )
        assert exc_info.value.resp.status == 412


# =============================================================================
# Test 11: manage_contacts_batch with field="phoneNumbers"
# =============================================================================


class TestBatchUpdateWithFieldParam:
    """Batch update sends contacts as dict (not list) with correct updateMask."""

    def _make_batch_service(self, batch_result=None):
        svc = MagicMock()
        svc.people.return_value.getBatchGet.return_value.execute.return_value = {
            "responses": [
                {"person": {"resourceName": "people/c1", "etag": "E1"}},
                {"person": {"resourceName": "people/c2", "etag": "E2"}},
            ]
        }
        svc.people.return_value.batchUpdateContacts.return_value.execute.return_value = (
            batch_result or {"updateResult": {}}
        )
        return svc

    def test_contacts_sent_as_dict_not_list(self):
        svc = self._make_batch_service()

        run(
            manage_contacts_batch(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                updates=[
                    {
                        "contact_id": "c1",
                        "phones": [{"number": "+79270000000", "type": "mobile"}],
                    },
                    {
                        "contact_id": "c2",
                        "phones": [{"number": "+78482123456", "type": "work"}],
                    },
                ],
                field="phoneNumbers",
            )
        )

        call_kwargs = svc.people.return_value.batchUpdateContacts.call_args.kwargs
        body = call_kwargs["body"]
        contacts = body["contacts"]
        assert isinstance(contacts, dict), f"Expected dict, got {type(contacts)}"

    def test_updateMask_matches_field_param(self):
        svc = self._make_batch_service()

        run(
            manage_contacts_batch(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                updates=[
                    {
                        "contact_id": "c1",
                        "phones": [{"number": "+79270000000", "type": "mobile"}],
                    },
                ],
                field="phoneNumbers",
            )
        )

        call_kwargs = svc.people.return_value.batchUpdateContacts.call_args.kwargs
        body = call_kwargs["body"]
        assert body["updateMask"] == "phoneNumbers"

    def test_each_contact_keyed_by_resource_name(self):
        svc = self._make_batch_service()

        run(
            manage_contacts_batch(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                updates=[
                    {
                        "contact_id": "c1",
                        "phones": [{"number": "+79270000000", "type": "mobile"}],
                    },
                    {
                        "contact_id": "c2",
                        "phones": [{"number": "+78482123456", "type": "work"}],
                    },
                ],
                field="phoneNumbers",
            )
        )

        call_kwargs = svc.people.return_value.batchUpdateContacts.call_args.kwargs
        contacts = call_kwargs["body"]["contacts"]
        assert "people/c1" in contacts
        assert "people/c2" in contacts

    def test_each_contact_has_etag(self):
        svc = self._make_batch_service()

        run(
            manage_contacts_batch(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                updates=[
                    {
                        "contact_id": "c1",
                        "phones": [{"number": "+79270000000", "type": "mobile"}],
                    },
                    {
                        "contact_id": "c2",
                        "phones": [{"number": "+78482123456", "type": "work"}],
                    },
                ],
                field="phoneNumbers",
            )
        )

        contacts = svc.people.return_value.batchUpdateContacts.call_args.kwargs["body"][
            "contacts"
        ]
        assert contacts["people/c1"]["etag"] == "E1"
        assert contacts["people/c2"]["etag"] == "E2"

    def test_person_body_contains_only_field_data(self):
        """Person body in contacts map must only contain the field key + etag."""
        svc = self._make_batch_service()

        run(
            manage_contacts_batch(
                service=svc,
                user_google_email="test@example.com",
                action="update",
                updates=[
                    {
                        "contact_id": "c1",
                        "phones": [{"number": "+79270000000", "type": "mobile"}],
                    },
                ],
                field="phoneNumbers",
            )
        )

        contacts = svc.people.return_value.batchUpdateContacts.call_args.kwargs["body"][
            "contacts"
        ]
        person = contacts["people/c1"]
        assert "phoneNumbers" in person
        assert "etag" in person
        assert "names" not in person
        assert "emailAddresses" not in person


# =============================================================================
# Test 12: manage_contacts_batch without field param raises UserInputError
# =============================================================================


class TestBatchUpdateWithoutFieldParam:
    """Batch update without field param must raise UserInputError, not hit the API."""

    def test_no_field_raises_user_input_error(self):
        svc = MagicMock()

        with pytest.raises(UserInputError) as exc_info:
            run(
                manage_contacts_batch(
                    service=svc,
                    user_google_email="test@example.com",
                    action="update",
                    updates=[
                        {
                            "contact_id": "c1",
                            "phones": [{"number": "+79270000000", "type": "mobile"}],
                        },
                    ],
                    field=None,
                )
            )

        assert "field" in str(exc_info.value).lower()
        svc.people.return_value.batchUpdateContacts.assert_not_called()

    def test_invalid_field_raises_user_input_error(self):
        svc = MagicMock()

        with pytest.raises(UserInputError) as exc_info:
            run(
                manage_contacts_batch(
                    service=svc,
                    user_google_email="test@example.com",
                    action="update",
                    updates=[
                        {
                            "contact_id": "c1",
                            "phones": [{"number": "+79270000000", "type": "mobile"}],
                        },
                    ],
                    field="invalidField",
                )
            )

        assert (
            "invalidField" in str(exc_info.value)
            or "field" in str(exc_info.value).lower()
        )
        svc.people.return_value.batchUpdateContacts.assert_not_called()


# =============================================================================
# Test 13: Deprecated aliases via manage_contact + DeprecationWarning
# =============================================================================


class TestManageContactDeprecatedAliases:
    """manage_contact passes deprecated phone/email aliases through and emits DeprecationWarning."""

    def test_phone_alias_works_and_warns(self):
        created = {
            "resourceName": "people/c_new",
            "phoneNumbers": [{"value": "+79270000000", "type": "mobile"}],
        }
        svc = MagicMock()
        svc.people.return_value.createContact.return_value.execute.return_value = (
            created
        )

        with pytest.warns(DeprecationWarning):
            run(
                manage_contact(
                    service=svc,
                    user_google_email="test@example.com",
                    action="create",
                    phone="+79270000000",
                )
            )

        call_kwargs = svc.people.return_value.createContact.call_args.kwargs
        body = call_kwargs["body"]
        assert "phoneNumbers" in body
        assert body["phoneNumbers"][0]["value"] == "+79270000000"

    def test_email_alias_works_and_warns(self):
        created = {
            "resourceName": "people/c_new",
            "emailAddresses": [{"value": "test@example.com", "type": "other"}],
        }
        svc = MagicMock()
        svc.people.return_value.createContact.return_value.execute.return_value = (
            created
        )

        with pytest.warns(DeprecationWarning):
            run(
                manage_contact(
                    service=svc,
                    user_google_email="test@example.com",
                    action="create",
                    email="test@example.com",
                )
            )

        call_kwargs = svc.people.return_value.createContact.call_args.kwargs
        body = call_kwargs["body"]
        assert "emailAddresses" in body
        assert body["emailAddresses"][0]["value"] == "test@example.com"


# =============================================================================
# Test 14: Simultaneous deprecated phone + phones — phones wins
# =============================================================================


class TestPhonesPriorityOverDeprecatedPhone:
    """When both phones and phone provided, phones list is used and phone alias ignored."""

    def test_phones_list_wins_over_deprecated_phone(self):
        created = {
            "resourceName": "people/c_new",
            "phoneNumbers": [{"value": "+79270000000", "type": "mobile"}],
        }
        svc = MagicMock()
        svc.people.return_value.createContact.return_value.execute.return_value = (
            created
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            run(
                manage_contact(
                    service=svc,
                    user_google_email="test@example.com",
                    action="create",
                    phones=[{"number": "+79270000000", "type": "mobile"}],
                    phone="+70000000000",
                )
            )
            assert any("ignored" in str(warning.message) for warning in w)

        call_kwargs = svc.people.return_value.createContact.call_args.kwargs
        body = call_kwargs["body"]
        assert len(body["phoneNumbers"]) == 1
        values = [p["value"] for p in body["phoneNumbers"]]
        assert "+70000000000" not in values
        assert "+79270000000" in values

    def test_deprecated_phone_not_duplicated_in_payload(self):
        """Ensure deprecated phone does not end up as extra entry alongside phones list."""
        created = {"resourceName": "people/c_new", "phoneNumbers": []}
        svc = MagicMock()
        svc.people.return_value.createContact.return_value.execute.return_value = (
            created
        )

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            run(
                manage_contact(
                    service=svc,
                    user_google_email="test@example.com",
                    action="create",
                    phones=[{"number": "+79270000000", "type": "mobile"}],
                    phone="+70000000000",
                )
            )

        body = svc.people.return_value.createContact.call_args.kwargs["body"]
        assert len(body["phoneNumbers"]) == 1


# =============================================================================
# Test 15: _format_contact with 3 phones of different types
# =============================================================================


class TestFormatContactThreePhones:
    """_format_contact renders multi-phone contact with correct labels per type."""

    def test_three_phones_different_types_show_block(self):
        person = {
            "resourceName": "people/c1",
            "phoneNumbers": [
                {"value": "+79270000000", "type": "mobile", "formattedType": "Mobile"},
                {"value": "+78482123456", "type": "work", "formattedType": "Work"},
                {"value": "250", "type": "internal"},
            ],
        }
        result = _format_contact(person)
        assert "Phones:" in result
        assert "  - +79270000000 (Mobile)" in result
        assert "  - +78482123456 (Work)" in result
        assert "  - 250 (Internal)" in result

    def test_internal_label_is_capital_I_Internal(self):
        """type='internal' must display as 'Internal' (capital I), not 'internal'."""
        person = {
            "resourceName": "people/c1",
            "phoneNumbers": [
                {"value": "+79270000000", "type": "mobile"},
                {"value": "250", "type": "internal"},
            ],
        }
        result = _format_contact(person)
        assert "250 (Internal)" in result
        assert "250 (internal)" not in result

    def test_three_phones_no_single_phone_line(self):
        """When multiple phones, must NOT use 'Phone: ...' single-line format."""
        person = {
            "resourceName": "people/c1",
            "phoneNumbers": [
                {"value": "+79270000000", "type": "mobile"},
                {"value": "+78482123456", "type": "work"},
                {"value": "250", "type": "internal"},
            ],
        }
        result = _format_contact(person)
        lines = result.split("\n")
        phone_lines = [line for line in lines if line.startswith("Phone:")]
        assert len(phone_lines) == 0, (
            "Multi-phone should use 'Phones:' block, not 'Phone:' line"
        )


# =============================================================================
# Test 16: _normalize_phone for internal short numbers
# =============================================================================


class TestNormalizePhoneInternal:
    """_normalize_phone must NOT mutate short internal numbers."""

    def test_short_number_250_unchanged(self):
        """'250' is an internal ATS number — must not be prefixed with + or modified."""
        assert _normalize_phone("250") == "250"

    def test_short_number_301_unchanged(self):
        assert _normalize_phone("301") == "301"

    def test_short_number_200_unchanged(self):
        assert _normalize_phone("200") == "200"

    def test_e164_number_normalized(self):
        """Real E.164 numbers are still normalized (strips formatting chars)."""
        assert _normalize_phone("+7 (927) 000-00-00") == "+79270000000"

    def test_internal_not_confused_with_e164(self):
        """Short internal number 250 must normalize to '250', not '+250' or '7250'."""
        result = _normalize_phone("250")
        assert not result.startswith("+")
        assert result == "250"

    def test_internal_dedup_works_correctly(self):
        """Two '250' entries normalize to same key — dedup works."""
        assert _normalize_phone("250") == _normalize_phone("250")

    def test_different_internal_numbers_are_distinct(self):
        """250 and 301 normalize to different keys — they stay separate."""
        assert _normalize_phone("250") != _normalize_phone("301")
