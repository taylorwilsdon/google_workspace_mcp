"""Integration tests for the Gmail-web faithful send/draft path.

Mocks all Google API calls (People + Gmail parent fetch); never hits the
network. Synthetic data only (example.com / example.org, generic names).
"""

import base64
import quopri
import re
from unittest.mock import Mock
import pytest
from gmail.gmail_tools import send_gmail_message


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _raw_sent(mock_service) -> str:
    kwargs = mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    raw = kwargs["body"]["raw"]
    return base64.urlsafe_b64decode(raw.encode()).decode("utf-8")


def _decode_bodies(raw: str) -> str:
    headers, _, body = raw.partition("\r\n\r\n")
    decoded = quopri.decodestring(body.encode("utf-8")).decode("utf-8")
    return f"{headers}\r\n\r\n{decoded}"


def _html_part(raw: str) -> str:
    """Return the decoded text/html part, regardless of CTE (QP or base64).

    Heavily non-ASCII bodies (Hebrew, Arabic) are base64-encoded by choose_cte,
    which the QP-only ``_decode_bodies`` cannot decode; let the stdlib parser
    handle either encoding.
    """
    from email import message_from_string

    parsed = message_from_string(raw)
    return "".join(
        part.get_payload(decode=True).decode("utf-8")
        for part in parsed.walk()
        if part.get_content_type() == "text/html"
    )


def _gmail_service():
    service = Mock()
    service.users().messages().send().execute.return_value = {"id": "sent123"}
    # No signature.
    service.users().settings().sendAs().list().execute.return_value = {"sendAs": []}
    return service


def _people_service_with(name: str, email: str):
    service = Mock()
    service.people().searchContacts().execute.return_value = {
        "results": [
            {
                "person": {
                    "names": [{"displayName": name}],
                    "emailAddresses": [{"value": email}],
                }
            }
        ]
    }
    # Warmup call (query="") is not used by our resolver, but keep it harmless.
    return service


def _people_service_empty():
    service = Mock()
    service.people().searchContacts().execute.return_value = {"results": []}
    return service


@pytest.mark.asyncio
async def test_send_hebrew_body_renders_rtl():
    """A Hebrew body auto-detects as RTL so Gmail right-aligns it."""
    gmail = _gmail_service()
    people = _people_service_empty()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="שלום עולם\n\nזה גוף ההודעה",
        include_signature=False,
    )

    html = _html_part(_raw_sent(gmail))
    assert '<div dir="rtl">' in html
    assert '<div dir="ltr">' not in html


@pytest.mark.asyncio
async def test_send_english_body_stays_ltr():
    """An English body stays LTR (byte-identical to historical output)."""
    gmail = _gmail_service()
    people = _people_service_empty()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    html = _html_part(_raw_sent(gmail))
    assert '<div dir="ltr">' in html
    assert '<div dir="rtl">' not in html


@pytest.mark.asyncio
async def test_send_direction_override_forces_rtl():
    """Explicit direction='rtl' wins even when the first strong char is LTR."""
    gmail = _gmail_service()
    people = _people_service_empty()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="OK שלום עולם",
        direction="rtl",
        include_signature=False,
    )

    html = _html_part(_raw_sent(gmail))
    assert '<div dir="rtl">' in html


@pytest.mark.asyncio
async def test_send_resolves_display_names_on_to():
    gmail = _gmail_service()
    people = _people_service_with("Ada Lovelace", "ada@example.com")

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "Email sent" in result
    assert "To: Ada Lovelace <ada@example.com>" in raw
    assert 'Content-Type: multipart/alternative; boundary="000000000000' in raw


@pytest.mark.asyncio
async def test_send_falls_back_to_bare_addr_when_unresolved():
    gmail = _gmail_service()
    people = _people_service_empty()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "To: ada@example.com" in raw
    # Still a full multipart/alternative.
    assert 'text/plain; charset="UTF-8"' in raw
    assert 'text/html; charset="UTF-8"' in raw


@pytest.mark.asyncio
async def test_send_degrades_when_name_lookup_raises():
    gmail = _gmail_service()
    people = Mock()
    people.people().searchContacts().execute.side_effect = RuntimeError("boom")

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "To: ada@example.com" in raw
    assert "multipart/alternative" in raw


@pytest.mark.asyncio
async def test_send_new_message_html_has_no_ai_or_tool_fingerprints():
    """Goal 2: the authored HTML must look hand-typed in Gmail web, not pasted
    from a tool. No class/style/p/data attributes in the new-body block, and
    none of the known AI/tool fingerprint tokens; no vendor mailer headers."""
    gmail = _gmail_service()
    people = _people_service_empty()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="First line\n\nSecond line",
        include_signature=False,
    )

    msg = _decode_bodies(_raw_sent(gmail))

    # The authored new-body block.
    block = re.search(r'(<div dir="ltr">.*?</div>)\r?\n?--', msg, re.DOTALL)
    assert block, msg
    new_body = block.group(1)

    # Typed Gmail structure: per-line <div>, blank line as <div><br></div>.
    assert "<div>First line</div>" in new_body
    assert "<div><br></div>" in new_body
    assert "<div>Second line</div>" in new_body

    # No fingerprints inside the new-body block.
    assert "class=" not in new_body
    assert "style=" not in new_body
    assert "<p " not in new_body and "<p>" not in new_body
    assert "data-" not in new_body
    for token in (
        "gmail-font-",
        "claude",
        "whitespace-normal",
        "break-words",
        "leading-[",
        "list-disc",
        "pl-",
    ):
        assert token not in msg.lower() if token == "claude" else token not in msg

    # No vendor mailer headers anywhere.
    header_block = msg.split("\r\n\r\n", 1)[0].lower()
    assert "x-mailer" not in header_block
    assert "user-agent" not in header_block


@pytest.mark.asyncio
async def test_send_without_people_service_appends_fallback_note(monkeypatch):
    """When the People (Contacts) scope is absent the decorator injects
    people_service=None; the send still succeeds with bare addresses AND the
    result tells the user resolution was skipped + how to enable it."""
    monkeypatch.setenv("GMAIL_EXTENDED_NAME_LOOKUP", "1")
    gmail = _gmail_service()

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=None,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "Email sent" in result
    assert "To: ada@example.com" in raw  # bare fallback, send not broken
    # The fallback is surfaced with the remediation path; with no People service
    # at all, all three resolution scopes are listed.
    assert "could not be resolved" in result
    assert "contacts.readonly" in result
    assert "contacts.other.readonly" in result
    assert "directory.readonly" in result
    assert "start_google_auth" in result


@pytest.mark.asyncio
async def test_send_no_note_when_people_service_present():
    """A present People service (even if it finds nothing) is not a missing-scope
    condition, so no fallback note is appended."""
    gmail = _gmail_service()
    people = _people_service_empty()

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    assert "Email sent" in result
    assert "could not be resolved" not in result


@pytest.mark.asyncio
async def test_send_no_note_when_all_names_supplied_inline():
    """No People service, but the caller supplied every display name inline, so
    resolution was never needed and the note must not fire."""
    gmail = _gmail_service()

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=None,
        user_google_email="grace@example.org",
        from_name="Grace Hopper",
        to="Ada Lovelace <ada@example.com>",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    assert "Email sent" in result
    assert "could not be resolved" not in result


class _FakeResp:
    def __init__(self, status):
        self.status = status
        self.reason = "Forbidden"


def _http_error(status: int):
    from googleapiclient.errors import HttpError

    return HttpError(_FakeResp(status), b'{"error":{"message":"insufficient scope"}}')


def _people_service_tiers(*, contacts=None, other=None, directory=None):
    """People mock with independently-configurable tiers (each a results payload,
    an HttpError to raise, or None for 'no match')."""
    service = Mock()

    def _wire(node, payload, person_wrapped=True):
        if isinstance(payload, Exception):
            node.execute.side_effect = payload
        elif payload is None:
            node.execute.return_value = {"results": [] if person_wrapped else []}
        else:
            node.execute.return_value = payload

    _wire(service.people().searchContacts(), contacts)
    _wire(service.otherContacts().search(), other)
    _wire(service.people().searchDirectoryPeople(), directory)
    return service


def _person_results(name, email, *, key="results", wrapped=True):
    person = {
        "names": [{"displayName": name}],
        "emailAddresses": [{"value": email}],
    }
    return {key: [{"person": person} if wrapped else person]}


@pytest.mark.asyncio
async def test_saved_contact_name_wins_over_thread_name():
    """Per Google's documented compose/reply resolution: when a reply recipient
    is BOTH a saved contact (under one name) AND in the thread under a different
    name, the saved Contacts name wins."""
    gmail = _gmail_service()
    # Thread says "MG Carroll"; saved contact says "Margaret C.".
    thread_full = {
        "messages": [
            {
                "id": "p1",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<parent@example.com>"},
                        {"name": "From", "value": "MG Carroll <mg@example.com>"},
                        {"name": "Subject", "value": "Project"},
                        {"name": "Date", "value": "Tue, 7 Apr 2026 19:19:00 +0000"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": _encode("Hi")},
                },
            }
        ]
    }
    gmail.users().threads().get().execute.return_value = thread_full
    gmail.users.return_value.threads.return_value.get.reset_mock()
    people = _people_service_tiers(
        contacts=_person_results("Maggie Carroll", "mg@example.com")
    )

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="you@example.org",
        to="mg@example.com",
        subject="Project",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
        quote_original=True,
    )

    raw = _raw_sent(gmail)
    # Saved contact wins in the To header...
    assert "To: Maggie Carroll <mg@example.com>" in raw
    assert "To: MG Carroll" not in raw
    # ...but the quote attribution still uses the thread's original sender name.
    assert "MG Carroll <mg@example.com> wrote:" in raw


@pytest.mark.asyncio
async def test_send_resolves_name_from_other_contacts(monkeypatch):
    """Scenario 2: a freshly-addressed external recipient not in saved contacts
    resolves via auto-collected 'Other contacts' (contacts.other.readonly)."""
    monkeypatch.setenv("GMAIL_EXTENDED_NAME_LOOKUP", "1")
    gmail = _gmail_service()
    people = _people_service_tiers(
        contacts={"results": []},
        other=_person_results("MG Carroll", "mg@anthropic.example"),
    )

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="mg@anthropic.example",
        subject="Intro",
        body="Hi",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "To: MG Carroll <mg@anthropic.example>" in raw
    assert "could not be resolved" not in result


@pytest.mark.asyncio
async def test_send_resolves_name_from_directory(monkeypatch):
    """Scenario 2: an internal colleague resolves via the Workspace directory
    (directory.readonly) when not in contacts or other-contacts."""
    monkeypatch.setenv("GMAIL_EXTENDED_NAME_LOOKUP", "1")
    gmail = _gmail_service()
    people = _people_service_tiers(
        contacts={"results": []},
        other={"results": []},
        directory=_person_results(
            "David Fishman", "david@example.org", key="people", wrapped=False
        ),
    )

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="david@example.org",
        subject="Sync",
        body="Hi",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "To: David Fishman <david@example.org>" in raw
    assert "could not be resolved" not in result


@pytest.mark.asyncio
async def test_send_note_lists_only_the_scope_that_errored(monkeypatch):
    """A 403 on the other-contacts tier records that scope; the note lists it
    (and directory if it also errors) but not tiers that simply found nothing."""
    monkeypatch.setenv("GMAIL_EXTENDED_NAME_LOOKUP", "1")
    gmail = _gmail_service()
    people = _people_service_tiers(
        contacts={"results": []},
        other=_http_error(403),
        directory={"people": []},
    )

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="stranger@example.com",
        subject="Hello",
        body="Hi",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "To: stranger@example.com" in raw  # bare
    assert "could not be resolved" in result
    assert "contacts.other.readonly" in result
    # Directory returned an empty result (not a scope error), so it is NOT listed.
    assert "directory.readonly" not in result


def _gmail_service_with_send_as(display_name: str, email: str):
    service = _gmail_service()
    service.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {
                "sendAsEmail": email,
                "displayName": display_name,
                "isPrimary": True,
                "signature": "",
            }
        ]
    }
    return service


def test_harvest_thread_display_names_first_name_wins():
    from gmail.gmail_tools import _harvest_thread_display_names

    messages = [
        {"from": "MG Carroll <mg@example.com>", "to": "you@example.org", "cc": ""},
        {"from": "mg@example.com", "to": "David Fishman <david@example.org>", "cc": ""},
    ]
    names = _harvest_thread_display_names(messages)
    assert names["mg@example.com"] == "MG Carroll"
    assert names["david@example.org"] == "David Fishman"
    # Bare address with no name anywhere is absent (caller emits it bare).
    assert "you@example.org" not in names


@pytest.mark.asyncio
async def test_reply_resolves_recipient_name_from_thread_without_people():
    """Scenario 1: replying without editing recipients resolves their names from
    the thread's own headers -- even with NO People service (zero extra scope),
    mirroring how Gmail shows an unsaved sender like 'MG Carroll'."""
    gmail = _gmail_service()
    thread_full = {
        "messages": [
            {
                "id": "p1",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<parent@example.com>"},
                        {"name": "From", "value": "MG Carroll <mg@example.com>"},
                        {"name": "Subject", "value": "Project"},
                        {"name": "Date", "value": "Tue, 7 Apr 2026 19:19:00 +0000"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": _encode("Hi there")},
                },
            }
        ]
    }
    gmail.users().threads().get().execute.return_value = thread_full
    gmail.users.return_value.threads.return_value.get.reset_mock()

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=None,  # no contacts scope at all
        user_google_email="you@example.org",
        to="mg@example.com",
        subject="Project",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
    )

    raw = _raw_sent(gmail)
    assert "To: MG Carroll <mg@example.com>" in raw
    # Name resolved from the thread, so no scope-fallback note.
    assert "could not be resolved" not in result


@pytest.mark.asyncio
async def test_send_as_display_name_populates_from():
    """The From line uses the Gmail Send-As displayName (what web shows), fetched
    once alongside the signature."""
    gmail = _gmail_service_with_send_as("Grace Hopper", "grace@example.org")
    people = _people_service_empty()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=True,
    )

    raw = _raw_sent(gmail)
    assert "From: Grace Hopper <grace@example.org>" in raw


@pytest.mark.asyncio
async def test_reply_builds_gmail_quote_from_parent():
    gmail = _gmail_service()
    # Parent thread fetch (full, with bodies) for the quote + auto-threading.
    thread_full = {
        "messages": [
            {
                "id": "p1",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<parent@example.com>"},
                        {"name": "From", "value": "Ada Lovelace <ada@example.com>"},
                        {"name": "Subject", "value": "Project sync"},
                        {
                            "name": "Date",
                            "value": "Tue, 7 Apr 2026 19:19:00 +0000",
                        },
                    ],
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _encode("Original line")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _encode("<div>Original line</div>")},
                        },
                    ],
                },
            }
        ]
    }
    gmail.users().threads().get().execute.return_value = thread_full
    people = _people_service_empty()
    # Reset call count so the assertion below measures only the send's fetch
    # (the setup line above already invoked .get() once).
    gmail.users.return_value.threads.return_value.get.reset_mock()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
        quote_original=True,
    )

    msg = _decode_bodies(_raw_sent(gmail))
    # The thread is fetched exactly once (shared by auto-threading + the quote).
    assert gmail.users.return_value.threads.return_value.get.call_count == 1
    # Auto-populated reply headers from the thread.
    assert "In-Reply-To: <parent@example.com>" in msg
    assert "References: <parent@example.com>" in " ".join(msg.split())
    # gmail_quote container + exact attribution + verbatim parent html.
    assert "gmail_quote gmail_quote_container" in msg
    assert "On Tue, 7 Apr 2026 at 19:19, Ada Lovelace" in msg
    assert "<div>Original line</div>" in msg
    assert "> Original line" in msg


@pytest.mark.asyncio
async def test_reply_without_parent_sends_without_quote():
    gmail = _gmail_service()
    gmail.users().threads().get().execute.side_effect = RuntimeError("fetch failed")
    people = _people_service_empty()

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
        quote_original=True,
    )

    msg = _decode_bodies(_raw_sent(gmail))
    assert "Email sent" in result
    assert "gmail_quote_container" not in msg
    # Still multipart with both parts.
    assert 'text/plain; charset="UTF-8"' in msg
    assert 'text/html; charset="UTF-8"' in msg


@pytest.mark.asyncio
async def test_send_rejects_crlf_header_injection_in_subject():
    """A CR/LF-laden subject must be rejected, not folded into extra headers."""
    gmail = _gmail_service()
    people = _people_service_empty()

    with pytest.raises(ValueError):
        await _unwrap(send_gmail_message)(
            service=gmail,
            people_service=people,
            user_google_email="grace@example.org",
            to="ada@example.com",
            subject="Meeting\r\nBcc: sneaky@example.com",
            body="Hello there",
            include_signature=False,
        )


@pytest.mark.asyncio
async def test_send_with_attachments_derives_reply_headers(monkeypatch):
    """Thread replies with attachments must derive In-Reply-To/References too,
    matching the no-attachments web path."""
    import gmail.gmail_tools as gt

    gmail = _gmail_service()
    people = _people_service_empty()

    async def fake_resolve(_attachments):
        return [{"data": b"x", "filename": "a.txt", "mime_type": "text/plain"}]

    async def fake_context(service, thread_id, in_reply_to=None, include_bodies=False):
        return {"message_ids": ["<first@example.com>", "<second@example.com>"]}

    captured = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return ("rawmsg", kwargs.get("thread_id"), 1, [])

    monkeypatch.setattr(gt, "_resolve_url_attachments", fake_resolve)
    monkeypatch.setattr(gt, "_fetch_thread_reply_context", fake_context)
    monkeypatch.setattr(gt, "_prepare_gmail_message", fake_prepare)

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Re: Project sync",
        body="See attached",
        thread_id="thread123",
        attachments=[{"filename": "a.txt", "content": "eA=="}],
        include_signature=False,
    )

    # Reply headers were derived from the thread message-id chain.
    assert captured["in_reply_to"] == "<second@example.com>"
    assert "<first@example.com>" in captured["references"]
    assert "<second@example.com>" in captured["references"]


def _plain_part(raw: str) -> str:
    from email import message_from_string

    parsed = message_from_string(raw)
    return "".join(
        part.get_payload(decode=True).decode("utf-8")
        for part in parsed.walk()
        if part.get_content_type() == "text/plain"
    )


@pytest.mark.asyncio
async def test_send_html_body_plain_part_keeps_paragraph_breaks():
    """<p>First.</p><p>Second.</p> must not flatten to 'First.Second.'."""
    gmail = _gmail_service()

    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=_people_service_empty(),
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Paragraphs",
        body="<p>First.</p><p>Second.</p>",
        body_format="html",
        include_signature=False,
    )

    plain = _plain_part(_raw_sent(gmail))
    assert "First.Second." not in plain
    assert "First.\nSecond." in plain.replace("\r\n", "\n")


def test_prepare_web_html_body_plain_part_keeps_paragraph_breaks():
    from gmail.gmail_tools import _prepare_gmail_message

    raw_b64, *_ = _prepare_gmail_message(
        subject="Paragraphs",
        body="<p>First.</p><p>Second.</p>",
        to="ada@example.com",
        body_format="html",
        from_email="grace@example.org",
        web_compose=True,
    )
    plain = _plain_part(base64.urlsafe_b64decode(raw_b64).decode("utf-8"))
    assert "First.Second." not in plain
    assert "First.\nSecond." in plain.replace("\r\n", "\n")


@pytest.mark.asyncio
async def test_send_rejects_recipient_header_with_no_parseable_address():
    """A non-empty To that parses to no address (semicolon separators) must be
    rejected, not silently dropped from the message."""
    from core.utils import UserInputError

    gmail = _gmail_service()

    with pytest.raises(UserInputError, match="Separate recipients with commas"):
        await _unwrap(send_gmail_message)(
            service=gmail,
            people_service=_people_service_empty(),
            user_google_email="grace@example.org",
            to="ada@example.com; alan@example.com",
            subject="Project sync",
            body="Hello there",
            include_signature=False,
        )
    send_calls = gmail.users.return_value.messages.return_value.send.call_args_list
    assert not [c for c in send_calls if "body" in c.kwargs]


@pytest.mark.asyncio
async def test_send_rejects_semicolon_recipients_even_with_permissive_parser(
    monkeypatch,
):
    """Python < 3.10.15 getaddresses splits "a; b" into addresses; the syntax
    must still be rejected rather than sent."""
    import gmail.gmail_tools as gmail_tools
    from core.utils import UserInputError

    monkeypatch.setattr(
        gmail_tools,
        "getaddresses",
        lambda values: [("", "ada@example.com"), ("", ""), ("", "alan@example.com")],
    )
    gmail = _gmail_service()

    with pytest.raises(UserInputError, match="Separate recipients with commas"):
        await _unwrap(send_gmail_message)(
            service=gmail,
            people_service=_people_service_empty(),
            user_google_email="grace@example.org",
            to="ada@example.com; alan@example.com",
            subject="Project sync",
            body="Hello there",
            include_signature=False,
        )
    send_calls = gmail.users.return_value.messages.return_value.send.call_args_list
    assert not [c for c in send_calls if "body" in c.kwargs]


@pytest.mark.asyncio
async def test_send_keeps_group_and_trailing_comma_recipients():
    gmail = _gmail_service()
    await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=_people_service_empty(),
        user_google_email="grace@example.org",
        to="Team: ada@example.com, alan@example.com;, bob@example.com,",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )
    headers = _raw_sent(gmail).split("\r\n\r\n", 1)[0]
    assert "To: Team: ada@example.com, alan@example.com;, bob@example.com" in headers


def _people_http_error(status: int, reason: str):
    import json

    import httplib2
    from googleapiclient.errors import HttpError

    content = json.dumps(
        {"error": {"code": status, "errors": [{"reason": reason}], "message": reason}}
    ).encode()
    return HttpError(httplib2.Response({"status": status}), content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reason,recorded",
    [
        (401, "authError", True),
        (403, "insufficientPermissions", True),
        (403, "accessNotConfigured", False),
        (403, "rateLimitExceeded", False),
        (403, "quotaExceeded", False),
    ],
)
async def test_people_tier_records_scope_only_for_auth_errors(status, reason, recorded):
    from gmail.gmail_tools import _people_search_tier

    request = Mock()
    request.return_value.execute.side_effect = _people_http_error(status, reason)
    missing: set = set()

    name = await _people_search_tier(
        request,
        {},
        "ada@example.com",
        results_key="results",
        wrap_key="person",
        scope="scope-x",
        missing_scopes=missing,
    )

    assert name is None
    assert (missing == {"scope-x"}) is recorded


def test_name_fallback_note_is_neutral_for_drafts(monkeypatch):
    from gmail import gmail_tools

    monkeypatch.setattr(gmail_tools, "is_oauth21_enabled", lambda: False)
    note = gmail_tools._build_name_fallback_note(True, None)
    assert "written as bare addresses" in note
    assert "sent as bare" not in note
    assert "start_google_auth" in note


def test_name_fallback_note_uses_oauth21_instruction(monkeypatch):
    from gmail import gmail_tools

    monkeypatch.setattr(gmail_tools, "is_oauth21_enabled", lambda: True)
    monkeypatch.setattr(gmail_tools, "is_external_oauth21_provider", lambda: False)
    note = gmail_tools._build_name_fallback_note(True, None)
    assert "start_google_auth" not in note
    assert "OAuth 2.1" in note

    monkeypatch.setattr(gmail_tools, "is_external_oauth21_provider", lambda: True)
    note = gmail_tools._build_name_fallback_note(True, None)
    assert "bearer token" in note


@pytest.mark.asyncio
@pytest.mark.parametrize("body_format", ["plain", "html"])
async def test_send_with_attachment_honors_direction(body_format):
    """Attachments take the web path too, so direction still applies."""
    gmail = _gmail_service()
    body = "<div>OK שלום</div>" if body_format == "html" else "OK שלום"

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body=body,
        body_format=body_format,
        direction="rtl",
        attachments=[{"filename": "a.txt", "content": "eA=="}],
        include_signature=False,
    )

    assert '<div dir="rtl">' in _html_part(_raw_sent(gmail))
