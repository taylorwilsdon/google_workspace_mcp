"""Tests that extract_office_xml_text distinguishes UNREADABLE from EMPTY.

Both used to return None, so a caller could not tell "this file is damaged"
from "this file contains no text". The two call for different responses, and
conflating them reports a corrupt document as an unsupported or empty one —
which sends the reader after the wrong problem.
"""

import io
import zipfile

import pytest

from core.utils import OfficeXmlExtractionError, extract_office_xml_text

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _docx(document_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def _valid(body: str) -> bytes:
    return _docx(
        f'<?xml version="1.0"?><w:document {W_NS}><w:body>{body}</w:body></w:document>'
    )


class TestUnreadableRaises:
    def test_not_a_zip_raises(self):
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(b"this is not a zip at all", DOCX_MIME)

    def test_truncated_zip_raises(self):
        good = _valid("<w:p><w:r><w:t>hello</w:t></w:r></w:p>")
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(good[: len(good) // 2], DOCX_MIME)

    def test_empty_bytes_raises(self):
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(b"", DOCX_MIME)

    def test_message_names_the_mime_type(self):
        """The report should say what it failed to read."""
        with pytest.raises(OfficeXmlExtractionError) as excinfo:
            extract_office_xml_text(b"not a zip", DOCX_MIME)
        assert DOCX_MIME in str(excinfo.value)

    def test_original_cause_is_chained(self):
        """`raise ... from e` keeps the underlying error for debugging."""
        with pytest.raises(OfficeXmlExtractionError) as excinfo:
            extract_office_xml_text(b"not a zip", DOCX_MIME)
        assert excinfo.value.__cause__ is not None


class TestMemberLevelFailuresAlsoRaise:
    """A per-member failure is still a failure to READ the file.

    Suppressing it and falling through to "no pieces -> None" presents a damaged
    document as an empty one, which is the exact conflation this change removes.
    """

    def test_malformed_document_xml_raises(self):
        blob = _docx('<?xml version="1.0"?><w:document><unclosed>')
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(blob, DOCX_MIME)

    def test_malformed_member_names_the_member(self):
        blob = _docx('<?xml version="1.0"?><w:document><unclosed>')
        with pytest.raises(OfficeXmlExtractionError) as excinfo:
            extract_office_xml_text(blob, DOCX_MIME)
        assert "word/document.xml" in str(excinfo.value)

    def test_malformed_shared_strings_raises(self):
        """Absent sharedStrings is optional; MALFORMED is not -- every t="s"
        cell resolves through it, so continuing yields wrong cell text."""
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                f'<?xml version="1.0"?><worksheet {ns}><sheetData></sheetData>'
                "</worksheet>",
            )
            zf.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst><unclosed>')
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(buf.getvalue(), XLSX_MIME)

    def test_absent_shared_strings_is_not_an_error(self):
        """The optional-member path must stay optional."""
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                f'<?xml version="1.0"?><worksheet {ns}><sheetData><row>'
                '<c r="A1" t="str"><v>alpha</v></c>'
                "</row></sheetData></worksheet>",
            )
        assert extract_office_xml_text(buf.getvalue(), XLSX_MIME) == "alpha"


class TestReadableButEmptyStillReturnsNone:
    def test_document_with_no_text_returns_none(self):
        """A valid file holding no text is NOT an error — the old contract."""
        assert extract_office_xml_text(_valid("<w:p/>"), DOCX_MIME) is None

    def test_whitespace_only_text_returns_none(self):
        body = '<w:p><w:r><w:t xml:space="preserve">   </w:t></w:r></w:p>'
        assert extract_office_xml_text(_valid(body), DOCX_MIME) is None

    def test_zip_without_the_expected_member_returns_none(self):
        """Readable ZIP, nothing to extract from — still empty, not damaged."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("unrelated.txt", "hello")
        assert extract_office_xml_text(buf.getvalue(), DOCX_MIME) is None


class TestSuccessPathUnchanged:
    def test_normal_document_still_extracts(self):
        blob = _valid("<w:p><w:r><w:t>hello world</w:t></w:r></w:p>")
        assert extract_office_xml_text(blob, DOCX_MIME) == "hello world"
