import io
import zipfile

import pytest

from core.utils import OfficeXmlExtractionError, extract_office_xml_text


@pytest.fixture(autouse=True)
def _no_ambient_office_xml_limit(monkeypatch):
    """The expansion limit is read from the environment on every extraction."""
    monkeypatch.delenv("WORKSPACE_MCP_MAX_OFFICE_XML_BYTES", raising=False)


OFFICE_PACKAGES = [
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
    ),
    (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>',
    ),
    (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst/></p:presentation>',
    ),
]


def _package(
    main_part: str, content_type: str, main_xml: str, *, include_main: bool = True
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/{main_part}" ContentType="{content_type}"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            f'Target="{main_part}"/></Relationships>',
        )
        if include_main:
            zf.writestr(main_part, main_xml)
    return buffer.getvalue()


def test_non_office_mime_returns_none():
    assert extract_office_xml_text(b"plain text", "text/plain") is None


@pytest.mark.parametrize("mime_type", [case[0] for case in OFFICE_PACKAGES])
def test_invalid_office_file_raises(mime_type):
    with pytest.raises(OfficeXmlExtractionError):
        extract_office_xml_text(b"not a zip", mime_type)


@pytest.mark.parametrize("mime_type,main_part,content_type,main_xml", OFFICE_PACKAGES)
def test_empty_office_file_returns_none(mime_type, main_part, content_type, main_xml):
    assert (
        extract_office_xml_text(_package(main_part, content_type, main_xml), mime_type)
        is None
    )


def test_docx_missing_main_part_raises():
    mime_type, main_part, content_type, main_xml = OFFICE_PACKAGES[0]
    with pytest.raises(OfficeXmlExtractionError):
        extract_office_xml_text(
            _package(main_part, content_type, main_xml, include_main=False), mime_type
        )
