"""Findings 20 and 50: Gmail attachment sources must each be bounded, and so must the total.

The local-path branch called `f.read()` and the inline-base64 branch called
`b64decode` with no ceiling, so one `draft_gmail_message` call could pull an arbitrary
amount into memory.
"""

import base64

import pytest

import gmail.gmail_tools as gmail_tools
from gmail.gmail_tools import _prepare_gmail_message


def _prepare(attachments):
    return _prepare_gmail_message(
        subject="s",
        body="b",
        to="to@example.com",
        attachments=attachments,
    )


class TestPathAttachments:
    def test_oversized_file_is_reported_not_attached(self, tmp_path, monkeypatch):
        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * 64)
        monkeypatch.setattr(gmail_tools, "MAX_EMAIL_ATTACHMENT_BYTES", 16)
        monkeypatch.setattr(gmail_tools, "validate_file_path", lambda _p: big)

        _raw, _thread, attached, errors = _prepare([{"path": str(big)}])

        assert attached == 0
        assert any("16 bytes" in e for e in errors)

    def test_file_within_limit_is_attached(self, tmp_path, monkeypatch):
        small = tmp_path / "small.txt"
        small.write_bytes(b"hello")
        monkeypatch.setattr(gmail_tools, "MAX_EMAIL_ATTACHMENT_BYTES", 1024)
        monkeypatch.setattr(gmail_tools, "validate_file_path", lambda _p: small)

        _raw, _thread, attached, errors = _prepare([{"path": str(small)}])

        assert attached == 1
        assert errors == []


class TestBase64Attachments:
    def test_oversized_encoded_content_is_rejected_before_decoding(self, monkeypatch):
        monkeypatch.setattr(gmail_tools, "MAX_EMAIL_ATTACHMENT_BYTES", 16)

        def _explode(*_args, **_kwargs):
            raise AssertionError("base64 must not be decoded past the limit")

        monkeypatch.setattr(gmail_tools.base64, "b64decode", _explode)

        _raw, _thread, attached, errors = _prepare(
            [
                {
                    "content": base64.b64encode(b"y" * 128).decode(),
                    "filename": "big.bin",
                }
            ]
        )

        assert attached == 0
        assert any("encoded length" in e for e in errors)

    def test_content_within_limit_is_attached(self, monkeypatch):
        monkeypatch.setattr(gmail_tools, "MAX_EMAIL_ATTACHMENT_BYTES", 1024)

        _raw, _thread, attached, errors = _prepare(
            [{"content": base64.b64encode(b"hello").decode(), "filename": "a.txt"}]
        )

        assert attached == 1
        assert errors == []


class TestTotalAcrossAttachments:
    def test_total_is_bounded_even_when_each_attachment_fits(self, monkeypatch):
        """A message of many just-under-the-limit attachments is the same DoS."""
        monkeypatch.setattr(gmail_tools, "MAX_EMAIL_ATTACHMENT_BYTES", 16)
        payload = base64.b64encode(b"z" * 10).decode()

        _raw, _thread, attached, errors = _prepare(
            [
                {"content": payload, "filename": "a.bin"},
                {"content": payload, "filename": "b.bin"},
                {"content": payload, "filename": "c.bin"},
            ]
        )

        # The first fits; the second pushes the running total past the cap.
        assert attached == 1
        assert any("total exceeds" in e for e in errors)

    def test_resolved_url_bytes_also_count_towards_the_total(self, monkeypatch):
        """URL-resolved bytes bypass the per-source branches, not the total."""
        monkeypatch.setattr(gmail_tools, "MAX_EMAIL_ATTACHMENT_BYTES", 16)

        _raw, _thread, attached, errors = _prepare(
            [
                {"_resolved_bytes": b"a" * 10, "filename": "a.bin"},
                {"_resolved_bytes": b"b" * 10, "filename": "b.bin"},
            ]
        )

        assert attached == 1
        assert any("total exceeds" in e for e in errors)


@pytest.mark.parametrize("limit_name", ["MAX_EMAIL_ATTACHMENT_BYTES"])
def test_limit_comes_from_core_limits(limit_name):
    from core import limits

    assert getattr(gmail_tools, limit_name) == getattr(limits, limit_name)
