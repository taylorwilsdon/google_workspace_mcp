"""Tests for extract_office_xml_text in core/utils.py.

Focus is text FIDELITY for Word documents: the extracted string has to be the
string a human reads, because callers search it. A search that silently misses
is worse than no search — it produces a confident "not present" that is wrong.
"""

import io
import struct
import zipfile

import pytest

import core.utils as utils
from core.utils import (
    OfficeXmlExtractionError,
    OfficeXmlTooLargeError,
    extract_office_xml_text,
)

W_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:future="urn:example:unsupported"'
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _docx(
    body: str,
    *,
    relationships: list[tuple[str, str, str]] | None = None,
    references: list[tuple[str, str]] | None = None,
    **members: str,
) -> bytes:
    """Minimal .docx: a body, plus any extra word/*.xml members by name."""
    if relationships is None:
        relationships = []
        for index, name in enumerate(members, start=1):
            if name.startswith("header"):
                kind = "header"
            elif name.startswith("footer"):
                kind = "footer"
            elif name in {"footnotes", "endnotes"}:
                kind = name
            else:
                continue
            relationships.append((f"rId{index}", kind, f"{name}.xml"))

    if references is None:
        references = [
            (kind, relationship_id)
            for relationship_id, kind, _ in relationships
            if kind in {"header", "footer"}
        ]

    section_properties = ""
    if references:
        section_properties = (
            "<w:sectPr>"
            + "".join(
                f'<w:{kind}Reference r:id="{relationship_id}" w:type="default"/>'
                for kind, relationship_id in references
            )
            + "</w:sectPr>"
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {W_NS}><w:body>{body}'
            f"{section_properties}</w:body>"
            "</w:document>",
        )
        for name, xml in members.items():
            zf.writestr(f"word/{name}.xml", xml)
        if relationships:
            relationship_xml = "".join(
                f'<Relationship Id="{relationship_id}" Type="{REL_BASE}/{kind}" '
                f'Target="{target}"/>'
                for relationship_id, kind, target in relationships
            )
            zf.writestr(
                "word/_rels/document.xml.rels",
                f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}">'
                f"{relationship_xml}</Relationships>",
            )
    return buf.getvalue()


def _hdr(body: str) -> str:
    return f'<?xml version="1.0"?><w:hdr {W_NS}>{body}</w:hdr>'


def _p(*runs: str) -> str:
    inner = "".join(f"<w:r><w:t>{r}</w:t></w:r>" for r in runs)
    return f"<w:p>{inner}</w:p>"


class TestRunsWithinAParagraph:
    def test_word_split_across_runs_is_not_broken_by_a_space(self):
        """The regression this suite exists for.

        Word splits a single word across runs constantly — spell-check state,
        formatting, tracked changes. Joining runs with a space turns one token
        into two, and every search for that token then fails.
        """
        blob = _docx(_p("Project", "X2024"))
        assert extract_office_xml_text(blob, DOCX_MIME) == "ProjectX2024"

    def test_xml_space_preserve_run_keeps_its_spacing(self):
        body = (
            '<w:p><w:r><w:t xml:space="preserve">hello </w:t></w:r>'
            "<w:r><w:t>world</w:t></w:r></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "hello world"

    def test_leading_and_trailing_whitespace_of_a_paragraph_is_trimmed(self):
        """Padded paragraph in the MIDDLE. A lone one is stripped by the final
        whole-output strip, so the per-paragraph property would go untested."""
        body = (
            _p("First.")
            + '<w:p><w:r><w:t xml:space="preserve">  padded  </w:t></w:r></w:p>'
            + _p("Last.")
        )
        assert (
            extract_office_xml_text(_docx(body), DOCX_MIME) == "First.\npadded\nLast."
        )

    def test_whitespace_only_paragraph_produces_no_blank_line(self):
        body = (
            _p("First.")
            + '<w:p><w:r><w:t xml:space="preserve">   </w:t></w:r></w:p>'
            + _p("Second.")
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "First.\nSecond."

    def test_runs_nested_in_a_hyperlink_still_join(self):
        """w:hyperlink puts runs a level deeper; the ancestor walk must reach it."""
        body = (
            '<w:p><w:r><w:t xml:space="preserve">see </w:t></w:r>'
            "<w:hyperlink><w:r><w:t>here</w:t></w:r></w:hyperlink>"
            '<w:r><w:t xml:space="preserve"> now</w:t></w:r></w:p>'
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "see here now"

    def test_tracked_insertion_does_not_split_a_token(self):
        """The flagship regression, reappearing through w:ins nesting."""
        body = (
            "<w:p><w:r><w:t>Project</w:t></w:r>"
            "<w:ins><w:r><w:t>X2024</w:t></w:r></w:ins></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "ProjectX2024"


class TestParagraphBoundaries:
    def test_paragraphs_are_newline_separated(self):
        blob = _docx(_p("First.") + _p("Second."))
        assert extract_office_xml_text(blob, DOCX_MIME) == "First.\nSecond."

    def test_empty_paragraphs_do_not_produce_blank_lines(self):
        blob = _docx(_p("First.") + "<w:p/>" + _p("Second."))
        assert extract_office_xml_text(blob, DOCX_MIME) == "First.\nSecond."


class TestHeadersFootersAndNotes:
    def test_header_text_is_included(self):
        blob = _docx(_p("body text"), header1=_hdr(_p("CONFIDENTIAL")))
        out = extract_office_xml_text(blob, DOCX_MIME)
        assert out == "body text\n\nCONFIDENTIAL"

    def test_body_comes_before_header_text(self):
        blob = _docx(_p("body text"), header1=_hdr(_p("CONFIDENTIAL")))
        out = extract_office_xml_text(blob, DOCX_MIME)
        assert out.index("body text") < out.index("CONFIDENTIAL")

    def test_footer_and_footnotes_are_included(self):
        blob = _docx(
            _p("body"),
            footer1=_hdr(_p("page footer")),
            footnotes=f'<?xml version="1.0"?><w:footnotes {W_NS}>'
            f"{_p('a footnote')}</w:footnotes>",
        )
        out = extract_office_xml_text(blob, DOCX_MIME)
        assert "page footer" in out
        assert "a footnote" in out

    def test_unrelated_word_members_are_not_scraped(self):
        """settings.xml and friends are configuration, not document text."""
        blob = _docx(
            _p("body"),
            settings=f'<?xml version="1.0"?><w:settings {W_NS}>'
            f"{_p('NOT DOCUMENT TEXT')}</w:settings>",
        )
        assert "NOT DOCUMENT TEXT" not in extract_office_xml_text(blob, DOCX_MIME)

    def test_relationship_target_does_not_need_a_conventional_filename(self):
        blob = _docx(
            _p("body"),
            relationships=[("rCustom", "header", "running-title.xml")],
            references=[("header", "rCustom")],
            **{"running-title": _hdr(_p("CUSTOM HEADER"))},
        )
        assert extract_office_xml_text(blob, DOCX_MIME) == "body\n\nCUSTOM HEADER"

    def test_unreferenced_header_relationship_is_not_scraped(self):
        blob = _docx(
            _p("body"),
            relationships=[("rUnused", "header", "header1.xml")],
            references=[],
            header1=_hdr(_p("STALE HEADER")),
        )
        assert extract_office_xml_text(blob, DOCX_MIME) == "body"

    def test_traversal_target_is_rejected(self):
        blob = _docx(
            _p("body"),
            relationships=[("rTraversal", "header", "../../etc/passwd")],
            references=[("header", "rTraversal")],
        )
        assert extract_office_xml_text(blob, DOCX_MIME) == "body"

    def test_absolute_url_target_is_rejected(self):
        blob = _docx(
            _p("body"),
            relationships=[("rUrl", "header", "https://evil.example/header1.xml")],
            references=[("header", "rUrl")],
        )
        assert extract_office_xml_text(blob, DOCX_MIME) == "body"

    def test_external_target_mode_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "word/document.xml",
                f'<?xml version="1.0"?><w:document {W_NS}><w:body>'
                f"{_p('body')}"
                '<w:sectPr><w:headerReference r:id="rExt" w:type="default"/>'
                "</w:sectPr></w:body></w:document>",
            )
            zf.writestr(
                "word/header1.xml",
                _hdr(_p("EXTERNAL HEADER")),
            )
            zf.writestr(
                "word/_rels/document.xml.rels",
                f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}">'
                f'<Relationship Id="rExt" Type="{REL_BASE}/header" '
                f'Target="header1.xml" TargetMode="External"/>'
                "</Relationships>",
            )
        assert extract_office_xml_text(buf.getvalue(), DOCX_MIME) == "body"


class TestNestedParagraphs:
    def test_text_box_inside_a_paragraph_is_not_emitted_twice(self):
        """Paragraphs nest: a text box inside a w:p carries its own w:p.

        Collecting with para.iter() attributes the inner text to BOTH the inner
        and the outer paragraph, so it appears twice.
        """
        body = (
            "<w:p><w:r><w:t>outer</w:t></w:r>"
            "<w:txbxContent><w:p><w:r><w:t>INNER</w:t></w:r></w:p></w:txbxContent>"
            "</w:p>"
        )
        out = extract_office_xml_text(_docx(body), DOCX_MIME)
        assert out.count("INNER") == 1, out

    def test_outer_and_inner_paragraph_text_are_both_present(self):
        body = (
            "<w:p><w:r><w:t>outer</w:t></w:r>"
            "<w:txbxContent><w:p><w:r><w:t>INNER</w:t></w:r></w:p></w:txbxContent>"
            "</w:p>"
        )
        out = extract_office_xml_text(_docx(body), DOCX_MIME)
        assert "outer" in out
        assert "INNER" in out

    def test_nested_paragraph_keeps_its_xml_position(self):
        body = (
            '<w:p><w:r><w:t xml:space="preserve">BEFORE </w:t></w:r>'
            "<w:r><w:drawing><wps:txbx><w:txbxContent>"
            "<w:p><w:r><w:t>INNER</w:t></w:r></w:p>"
            "</w:txbxContent></wps:txbx></w:drawing></w:r>"
            '<w:r><w:t xml:space="preserve"> AFTER</w:t></w:r></w:p>'
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "BEFORE\nINNER\nAFTER"


class TestMarkupCompatibility:
    """Word writes a text box TWICE: mc:Choice for modern readers, mc:Fallback
    (VML) for old ones. Both carry the same text."""

    _CHOICE = (
        '<mc:Choice Requires="wps"><w:drawing><wps:txbx><w:txbxContent>'
        "<w:p><w:r><w:t>BOXED</w:t></w:r></w:p>"
        "</w:txbxContent></wps:txbx></w:drawing></mc:Choice>"
    )
    _FALLBACK = (
        "<mc:Fallback><w:pict><v:textbox><w:txbxContent>"
        "<w:p><w:r><w:t>BOXED</w:t></w:r></w:p>"
        "</w:txbxContent></v:textbox></w:pict></mc:Fallback>"
    )

    def test_text_box_content_is_not_emitted_once_per_alternative(self):
        body = (
            "<w:p><w:r><w:t>before</w:t></w:r><w:r><mc:AlternateContent>"
            + self._CHOICE
            + self._FALLBACK
            + "</mc:AlternateContent></w:r>"
            "<w:r><w:t>after</w:t></w:r></w:p>"
        )
        out = extract_office_xml_text(_docx(body), DOCX_MIME)
        assert out.count("BOXED") == 1, out

    def test_fallback_only_content_is_still_extracted(self):
        """No Choice to prefer, so the Fallback must NOT be skipped."""
        body = (
            "<w:p><w:r><mc:AlternateContent>"
            + self._FALLBACK
            + "</mc:AlternateContent></w:r></w:p>"
        )
        out = extract_office_xml_text(_docx(body), DOCX_MIME)
        assert "BOXED" in out

    def test_unsupported_choice_uses_text_bearing_fallback(self):
        choice = (
            '<mc:Choice Requires="future"><future:shape>'
            "<w:p><w:r><w:t>UNSUPPORTED</w:t></w:r></w:p>"
            "</future:shape></mc:Choice>"
        )
        fallback = (
            "<mc:Fallback><w:p><w:r><w:t>READABLE FALLBACK</w:t></w:r></w:p>"
            "</mc:Fallback>"
        )
        body = (
            f"<w:p><w:r><mc:AlternateContent>{choice}{fallback}"
            "</mc:AlternateContent></w:r></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "READABLE FALLBACK"

    def test_supported_choice_wins_over_fallback(self):
        choice = (
            '<mc:Choice Requires="wps"><wps:txbx><w:txbxContent>'
            "<w:p><w:r><w:t>MODERN</w:t></w:r></w:p>"
            "</w:txbxContent></wps:txbx></mc:Choice>"
        )
        fallback = "<mc:Fallback><w:p><w:r><w:t>LEGACY</w:t></w:r></w:p></mc:Fallback>"
        body = (
            f"<w:p><w:r><mc:AlternateContent>{choice}{fallback}"
            "</mc:AlternateContent></w:r></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "MODERN"

    def test_empty_supported_choice_uses_readable_fallback(self):
        body = (
            '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps">'
            "<wps:txbx/></mc:Choice><mc:Fallback>"
            "<w:p><w:r><w:t>FALLBACK TEXT</w:t></w:r></w:p>"
            "</mc:Fallback></mc:AlternateContent></w:r></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "FALLBACK TEXT"

    def test_choice_only_unsupported_retains_text(self):
        """Choice-only AlternateContent with unsupported Requires must not
        discard all text; the Choice is the only branch available."""
        body = (
            "<w:p><w:r><mc:AlternateContent>"
            '<mc:Choice Requires="future"><future:shape>'
            "<w:p><w:r><w:t>ONLY BRANCH</w:t></w:r></w:p>"
            "</future:shape></mc:Choice>"
            "</mc:AlternateContent></w:r></w:p>"
        )
        assert "ONLY BRANCH" in extract_office_xml_text(_docx(body), DOCX_MIME)


class TestTables:
    def test_real_table_cells_are_separate_lines(self):
        """A real table is w:tbl > w:tr > w:tc > w:p, i.e. the PARAGRAPH path."""
        body = (
            "<w:tbl><w:tr>"
            "<w:tc><w:p><w:r><w:t>cell A</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>cell B</w:t></w:r></w:p></w:tc>"
            "</w:tr></w:tbl>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "cell A\ncell B"


class TestTextOutsideAnyParagraph:
    """Defensive path: text with no w:p ancestor, neither dropped nor mangled."""

    def test_orphan_runs_join_like_a_paragraph(self):
        blob = _docx(
            _p("body text")
            + "<w:tbl><w:r><w:t>Project</w:t></w:r><w:r><w:t>X2024</w:t></w:r></w:tbl>"
        )
        assert extract_office_xml_text(blob, DOCX_MIME) == "body text\nProjectX2024"

    def test_orphan_text_keeps_its_document_position(self):
        blob = _docx(
            "<w:tbl><w:r><w:t>ORPHAN FIRST</w:t></w:r></w:tbl>" + _p("body text")
        )
        assert extract_office_xml_text(blob, DOCX_MIME) == "ORPHAN FIRST\nbody text"


class TestTabsAndBreaks:
    def test_tab_between_runs_is_a_tab_not_a_fused_token(self):
        body = (
            "<w:p><w:r><w:t>A</w:t></w:r><w:r><w:tab/></w:r>"
            "<w:r><w:t>B</w:t></w:r></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "A\tB"

    def test_break_inside_a_run_becomes_a_newline(self):
        body = "<w:p><w:r><w:t>A</w:t><w:br/><w:t>B</w:t></w:r></w:p>"
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "A\nB"

    def test_tab_stop_definitions_are_not_tab_characters(self):
        """w:tab under w:pPr/w:tabs defines a stop; it is not content."""
        body = (
            "<w:p><w:pPr><w:tabs><w:tab w:val='left' w:pos='720'/></w:tabs></w:pPr>"
            "<w:r><w:t>AB</w:t></w:r></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "AB"

    def test_boundary_break_and_tab_are_not_stripped(self):
        body = (
            _p("First")
            + "<w:p><w:r><w:br/><w:t>Second</w:t></w:r></w:p>"
            + "<w:p><w:r><w:tab/><w:t>Third</w:t></w:r></w:p>"
        )
        assert (
            extract_office_xml_text(_docx(body), DOCX_MIME)
            == "First\n\nSecond\n\tThird"
        )


class TestPowerPoint:
    def test_slide_runs_join_without_a_space(self):
        buf = io.BytesIO()
        a_ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ppt/presentation.xml", "<presentation/>")
            zf.writestr(
                "ppt/slides/slide1.xml",
                f'<?xml version="1.0"?><root {a_ns}>'
                "<a:p><a:r><a:t>Slide</a:t></a:r><a:r><a:t>Title1</a:t></a:r></a:p>"
                "</root>",
            )
        assert extract_office_xml_text(buf.getvalue(), PPTX_MIME) == "SlideTitle1"

    def test_drawingml_break_between_runs_is_preserved(self):
        buf = io.BytesIO()
        a_ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ppt/presentation.xml", "<presentation/>")
            zf.writestr(
                "ppt/slides/slide1.xml",
                f'<?xml version="1.0"?><root {a_ns}>'
                "<a:p><a:r><a:t>A</a:t></a:r><a:br/>"
                "<a:r><a:t>B</a:t></a:r></a:p></root>",
            )
        assert extract_office_xml_text(buf.getvalue(), PPTX_MIME) == "A\nB"


class TestFallbackAndOtherFormats:
    def test_spreadsheet_cells_remain_space_joined(self):
        """Sheets have no paragraphs; this fix must not change their behaviour."""
        buf = io.BytesIO()
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("xl/workbook.xml", "<workbook/>")
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                f'<?xml version="1.0"?><worksheet {ns}><sheetData><row>'
                '<c r="A1" t="str"><v>alpha</v></c>'
                '<c r="B1" t="str"><v>beta</v></c>'
                "</row></sheetData></worksheet>",
            )
        out = extract_office_xml_text(buf.getvalue(), XLSX_MIME)
        # Exact, not containment: containment would also pass if spreadsheet
        # values became newline-joined or concatenated, which is the very thing
        # this test exists to prevent.
        assert out == "alpha beta"


SHEET_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _zip(**members: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, xml in members.items():
            zf.writestr(name, xml)
    return buf.getvalue()


def _xlsx(**members: str) -> bytes:
    return _zip(**{"xl/workbook.xml": "<workbook/>", **members})


def _shared_string_sheet(value: str) -> str:
    return (
        f'<worksheet {SHEET_NS}><sheetData><row><c r="A1" t="s"><v>{value}</v></c>'
        "</row></sheetData></worksheet>"
    )


class TestDamagedParts:
    """A damaged part must not degrade into partial text or a false 'empty'."""

    def test_malformed_related_word_part_raises(self):
        data = _docx(_p("Body"), header1="<w:hdr")
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(data, DOCX_MIME)

    def test_missing_related_word_part_raises(self):
        data = _docx(_p("Body"), relationships=[("rId1", "header", "header1.xml")])
        with pytest.raises(OfficeXmlExtractionError, match="header1.xml"):
            extract_office_xml_text(data, DOCX_MIME)

    def test_malformed_word_relationships_raise(self):
        buf = io.BytesIO(_docx(_p("Body")))
        with zipfile.ZipFile(buf, "a") as zf:
            zf.writestr("word/_rels/document.xml.rels", "<Relationships")
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(buf.getvalue(), DOCX_MIME)

    @pytest.mark.parametrize(
        ("mime_type", "part", "members"),
        [
            (PPTX_MIME, "ppt/presentation.xml", {"ppt/slides/slide1.xml": "<sld/>"}),
            (XLSX_MIME, "xl/workbook.xml", {"xl/worksheets/sheet1.xml": "<ws/>"}),
        ],
    )
    def test_missing_primary_part_raises(self, mime_type, part, members):
        with pytest.raises(OfficeXmlExtractionError, match=part):
            extract_office_xml_text(_zip(**members), mime_type)

    @pytest.mark.parametrize(
        ("mime_type", "part"),
        [(PPTX_MIME, "ppt/presentation.xml"), (XLSX_MIME, "xl/workbook.xml")],
    )
    def test_malformed_primary_part_raises(self, mime_type, part):
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(_zip(**{part: "<root"}), mime_type)

    def test_malformed_worksheet_raises(self):
        data = _xlsx(**{"xl/worksheets/sheet1.xml": "<worksheet"})
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(data, XLSX_MIME)

    def test_malformed_shared_strings_raises(self):
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": f"<worksheet {SHEET_NS}/>",
                "xl/sharedStrings.xml": "<sst",
            }
        )
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(data, XLSX_MIME)

    def test_shared_string_reference_without_part_raises(self):
        data = _xlsx(**{"xl/worksheets/sheet1.xml": _shared_string_sheet("0")})
        with pytest.raises(OfficeXmlExtractionError, match="sharedStrings.xml"):
            extract_office_xml_text(data, XLSX_MIME)

    @pytest.mark.parametrize(
        ("value", "message"), [("1", "out of range"), ("x", "non-integer")]
    )
    def test_invalid_shared_string_index_raises(self, value, message):
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": _shared_string_sheet(value),
                "xl/sharedStrings.xml": f"<sst {SHEET_NS}><si><t>only</t></si></sst>",
            }
        )
        with pytest.raises(OfficeXmlExtractionError, match=message):
            extract_office_xml_text(data, XLSX_MIME)

    def test_valid_shared_string_resolves(self):
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": _shared_string_sheet("0"),
                "xl/sharedStrings.xml": f"<sst {SHEET_NS}><si><t>only</t></si></sst>",
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "only"


ENV = "WORKSPACE_MCP_MAX_OFFICE_XML_BYTES"
A_NS = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _deflated(**members: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, xml in members.items():
            zf.writestr(name, xml)
    return buf.getvalue()


def _sheet(text: str) -> str:
    return (
        f'<worksheet {SHEET_NS}><sheetData><row><c r="A1" t="str"><v>{text}</v></c>'
        "</row></sheetData></worksheet>"
    )


def _understate_size(archive: bytes, name: str, claimed: int) -> bytes:
    """Rewrite a member's declared uncompressed size in BOTH of its headers.

    The compressed data is left alone, so the member still inflates to its real
    size: the archive now lies about how big it is.
    """
    data = bytearray(archive)
    encoded = name.encode()
    patched = 0
    # (signature, offset of the uncompressed-size field, offset of the name)
    for signature, size_at, name_at in (
        (b"PK\x03\x04", 22, 30),
        (b"PK\x01\x02", 24, 46),
    ):
        start = 0
        while (at := data.find(signature, start)) != -1:
            start = at + 4
            if data[at + name_at : at + name_at + len(encoded)] == encoded:
                data[at + size_at : at + size_at + 4] = struct.pack("<I", claimed)
                patched += 1
    assert patched == 2, "expected one local and one central header"
    return bytes(data)


@pytest.fixture(autouse=True)
def _no_ambient_limit(monkeypatch):
    """The limit comes from the environment; a developer's shell must not set it."""
    monkeypatch.delenv(ENV, raising=False)


def _archive(compression: int, **members: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, xml in members.items():
            zf.writestr(name, xml)
    return buf.getvalue()


class TestExpansionLimits:
    """An Office file is a ZIP: a small download can expand without bound.

    The limit is set low here so every archive stays tiny; the behaviour under
    test does not depend on its size.
    """

    def test_ordinary_files_are_unaffected_by_the_default_limit(self):
        assert (
            extract_office_xml_text(_docx(_p("<w:r><w:t>Body</w:t></w:r>")), DOCX_MIME)
            == "Body"
        )
        assert (
            extract_office_xml_text(
                _xlsx(**{"xl/worksheets/sheet1.xml": _sheet("cell")}), XLSX_MIME
            )
            == "cell"
        )
        pptx = _zip(
            **{
                "ppt/presentation.xml": "<presentation/>",
                "ppt/slides/slide1.xml": f"<root {A_NS}><a:p><a:r><a:t>Slide</a:t></a:r></a:p></root>",
            }
        )
        assert extract_office_xml_text(pptx, PPTX_MIME) == "Slide"

    def test_a_small_download_that_expands_past_the_limit_is_rejected(
        self, monkeypatch
    ):
        monkeypatch.setenv(ENV, "2000")
        data = _deflated(
            **{
                "xl/workbook.xml": "<workbook/>",
                "xl/worksheets/sheet1.xml": _sheet("A" * 50_000),
            }
        )
        assert len(data) < 1_000  # the download itself is unremarkable
        with pytest.raises(OfficeXmlTooLargeError, match="sheet1.xml") as exc:
            extract_office_xml_text(data, XLSX_MIME)
        # One limit, so the message can say exactly which number was exceeded.
        assert "2,000 bytes" in str(exc.value)
        assert ENV in str(exc.value)
        # No tool here converts a file already in Drive, so the way forward
        # named is Drive's own conversion, after which nothing is unzipped.
        assert "Save as Google Docs, Sheets or Slides" in str(exc.value)
        # The limit is per file, so the part is where it was exceeded, not a
        # part that is itself over a per-part cap.
        assert "exceeded while reading xl/worksheets/sheet1.xml" in str(exc.value)

    def test_parts_that_each_fit_are_rejected_once_their_total_does_not(
        self, monkeypatch
    ):
        monkeypatch.setenv(ENV, "5000")
        sheets = {f"xl/worksheets/sheet{n}.xml": _sheet("A" * 2_000) for n in (1, 2, 3)}
        data = _deflated(**{"xl/workbook.xml": "<workbook/>", **sheets})
        with pytest.raises(OfficeXmlTooLargeError, match="sheet3.xml"):
            extract_office_xml_text(data, XLSX_MIME)

    def test_repeated_shared_strings_cannot_amplify_the_extracted_text(
        self, monkeypatch
    ):
        """A shared string is stored once but may be referenced by many cells."""
        monkeypatch.setenv(ENV, "20000")
        cells = "".join(f'<c r="A{row}" t="s"><v>0</v></c>' for row in range(1, 301))
        members = {
            "xl/workbook.xml": "<workbook/>",
            "xl/sharedStrings.xml": (
                f"<sst {SHEET_NS}><si><t>{'A' * 5_000}</t></si></sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                f"<worksheet {SHEET_NS}><sheetData><row>{cells}</row></sheetData>"
                "</worksheet>"
            ),
        }
        assert sum(len(xml.encode()) for xml in members.values()) < 20_000

        with pytest.raises(OfficeXmlTooLargeError, match="extracted text") as exc:
            extract_office_xml_text(_deflated(**members), XLSX_MIME)

        assert "extracting text from xl/worksheets/sheet1.xml" in str(exc.value)

    @pytest.mark.parametrize(
        ("mime_type", "members", "culprit"),
        [
            (
                XLSX_MIME,
                {
                    "xl/workbook.xml": "<workbook/>",
                    "xl/sharedStrings.xml": f"<sst {SHEET_NS}><si><t>{'A' * 9_000}</t></si></sst>",
                },
                "sharedStrings.xml",
            ),
            (
                DOCX_MIME,
                {
                    "word/document.xml": f"<w:document {W_NS}><w:body/></w:document>",
                    "word/_rels/document.xml.rels": f'<Relationships xmlns="{REL_NS}">{" " * 9_000}</Relationships>',
                },
                "document.xml.rels",
            ),
            (
                DOCX_MIME,
                {
                    "word/document.xml": (
                        f"<w:document {W_NS}><w:body><w:sectPr>"
                        '<w:headerReference w:type="default" r:id="rId1"/>'
                        "</w:sectPr></w:body></w:document>"
                    ),
                    "word/_rels/document.xml.rels": (
                        f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" '
                        f'Type="{REL_BASE}/header" Target="header1.xml"/></Relationships>'
                    ),
                    "word/header1.xml": f"<w:hdr {W_NS}>{' ' * 9_000}</w:hdr>",
                },
                "header1.xml",
            ),
            (
                PPTX_MIME,
                {"ppt/presentation.xml": f"<presentation>{' ' * 9_000}</presentation>"},
                "presentation.xml",
            ),
            (
                PPTX_MIME,
                {
                    "ppt/presentation.xml": "<presentation/>",
                    "ppt/slides/slide1.xml": f"<root {A_NS}>{' ' * 9_000}</root>",
                },
                "slide1.xml",
            ),
        ],
        ids=["shared-strings", "word-rels", "word-header", "presentation", "slide"],
    )
    def test_every_kind_of_part_draws_on_the_same_budget(
        self, monkeypatch, mime_type, members, culprit
    ):
        """No read in the extractor bypasses the limit, optional parts included."""
        monkeypatch.setenv(ENV, "4000")
        with pytest.raises(OfficeXmlTooLargeError, match=culprit):
            extract_office_xml_text(_deflated(**members), mime_type)

    def test_the_limit_is_inclusive(self, monkeypatch):
        """A file that expands to exactly the limit is read; one byte more is not."""
        members = {
            "xl/workbook.xml": "<workbook/>",
            "xl/worksheets/sheet1.xml": _sheet("cell"),
        }
        total = sum(len(xml.encode()) for xml in members.values())
        data = _deflated(**members)

        monkeypatch.setenv(ENV, str(total))
        assert extract_office_xml_text(data, XLSX_MIME) == "cell"

        monkeypatch.setenv(ENV, str(total - 1))
        with pytest.raises(OfficeXmlTooLargeError):
            extract_office_xml_text(data, XLSX_MIME)

    @pytest.mark.parametrize(
        ("codec", "compression"),
        [("bz2", zipfile.ZIP_BZIP2), ("lzma", zipfile.ZIP_LZMA)],
    )
    def test_compression_the_counted_read_cannot_bound_is_refused(
        self, monkeypatch, codec, compression
    ):
        """zipfile bounds read(n) for DEFLATE only; BZIP2 and LZMA inflate a whole
        block first. Office files may not use them, so they are refused unread."""
        pytest.importorskip(codec)
        monkeypatch.setenv(ENV, "100000")
        data = _archive(
            compression,
            **{
                "xl/workbook.xml": "<workbook/>",
                "xl/worksheets/sheet1.xml": _sheet("cell"),
            },
        )
        with pytest.raises(OfficeXmlExtractionError, match="compression") as exc:
            extract_office_xml_text(data, XLSX_MIME)
        assert not isinstance(exc.value, OfficeXmlTooLargeError)

    def test_an_archive_that_understates_its_size_is_still_rejected(self, monkeypatch):
        """The declared size is a claim. This archive says 100 bytes and holds 50,000."""
        monkeypatch.setenv(ENV, "2000")
        honest = _deflated(
            **{
                "xl/workbook.xml": "<workbook/>",
                "xl/worksheets/sheet1.xml": _sheet("A" * 50_000),
            }
        )
        lying = _understate_size(honest, "xl/worksheets/sheet1.xml", 100)
        with zipfile.ZipFile(io.BytesIO(lying)) as zf:
            assert zf.getinfo("xl/worksheets/sheet1.xml").file_size == 100
        with pytest.raises(OfficeXmlExtractionError):
            extract_office_xml_text(lying, XLSX_MIME)

    def test_the_read_is_counted_whatever_the_header_said(self):
        """Independent of how zipfile treats a lying header: the counter stops
        the read just past the limit instead of draining the member."""
        served = []

        class _Member(io.BytesIO):
            def read(self, size=-1):
                chunk = super().read(size)
                served.append(len(chunk))
                return chunk

        class _LyingZip:
            def getinfo(self, name):
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.file_size = 10
                return info

            def open(self, info):
                return _Member(b"A" * 1_000_000)

        with pytest.raises(OfficeXmlTooLargeError):
            utils._read_zip_member(
                _LyingZip(), "part.xml", utils._ExpansionBudget(2_000)
            )
        assert sum(served) == 2_001

    def test_a_declared_oversize_part_is_rejected_without_inflating_it(self):
        class _HonestZip:
            def getinfo(self, name):
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.file_size = 2_001
                return info

            def open(self, info):
                raise AssertionError("the member was opened")

        with pytest.raises(OfficeXmlTooLargeError):
            utils._read_zip_member(
                _HonestZip(), "part.xml", utils._ExpansionBudget(2_000)
            )

    def test_zero_disables_the_limit(self, monkeypatch):
        monkeypatch.setenv(ENV, "0")
        data = _deflated(
            **{
                "xl/workbook.xml": "<workbook/>",
                "xl/worksheets/sheet1.xml": _sheet("A" * 50_000),
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "A" * 50_000

    def test_an_invalid_setting_is_a_configuration_error_not_a_damaged_file(
        self, monkeypatch
    ):
        monkeypatch.setenv(ENV, "5MB")
        data = _xlsx(**{"xl/worksheets/sheet1.xml": _sheet("cell")})
        with pytest.raises(ValueError, match=ENV):
            extract_office_xml_text(data, XLSX_MIME)

    def test_too_large_is_still_an_extraction_error(self):
        """Callers that predate the subclass must keep stopping cleanly."""
        assert issubclass(OfficeXmlTooLargeError, OfficeXmlExtractionError)


class TestChoiceNamespaceScope:
    """mc:Choice/@Requires names PREFIXES, so each Choice is resolved against the
    prefix map in scope where it starts. The maps are shared while the scope is
    unchanged, so they must be refreshed at every scope change, in and out."""

    _MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    _W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"

    @classmethod
    def _alternate(cls, label: str) -> str:
        return (
            '<mc:AlternateContent><mc:Choice Requires="x">'
            f"<w:p><w:r><w:t>CHOICE-{label}</w:t></w:r></w:p></mc:Choice>"
            f"<mc:Fallback><w:p><w:r><w:t>FALLBACK-{label}</w:t></w:r></w:p>"
            "</mc:Fallback></mc:AlternateContent>"
        )

    def _document(self) -> str:
        # x is supported at the root, shadowed by an unsupported namespace inside
        # the middle wrapper only, and supported again once that wrapper ends.
        return (
            f'<w:document xmlns:w="{self._W}" xmlns:mc="{self._MC}" '
            f'xmlns:x="{self._WPS}"><w:body>'
            f"{self._alternate('1')}"
            f'<w:sdt xmlns:x="urn:example:unsupported">{self._alternate("2")}'
            f"{self._alternate('3')}</w:sdt>"
            f"{self._alternate('4')}"
            "</w:body></w:document>"
        )

    def test_each_choice_sees_the_prefix_map_in_scope_where_it_starts(self):
        root, maps = utils._parse_xml_with_choice_namespaces(self._document().encode())
        choices = list(root.iter(f"{{{self._MC}}}Choice"))
        assert [maps[choice]["x"] for choice in choices] == [
            self._WPS,
            "urn:example:unsupported",
            "urn:example:unsupported",
            self._WPS,
        ]

    def test_a_shadowed_prefix_selects_the_fallback_only_while_shadowed(self):
        data = _zip(**{"word/document.xml": self._document()})
        assert extract_office_xml_text(data, DOCX_MIME) == (
            "CHOICE-1\nFALLBACK-2\nFALLBACK-3\nCHOICE-4"
        )

    def test_choices_in_an_unchanged_scope_share_one_map(self):
        """Copying the map per Choice cost (prefixes x Choice elements)."""
        root, maps = utils._parse_xml_with_choice_namespaces(self._document().encode())
        first, second, third, fourth = root.iter(f"{{{self._MC}}}Choice")
        assert maps[second] is maps[third]
        assert maps[first] is not maps[second]
        assert maps[fourth] is not maps[third]
        assert maps[first] == maps[fourth]


STRICT_SHEET_NS = 'xmlns="http://purl.oclc.org/ooxml/spreadsheetml/main"'


def _cells_sheet(cells: str, ns: str = SHEET_NS) -> str:
    return f"<worksheet {ns}><sheetData><row>{cells}</row></sheetData></worksheet>"


def _sst(*entries: str, ns: str = SHEET_NS) -> str:
    return f"<sst {ns}>" + "".join(f"<si>{e}</si>" for e in entries) + "</sst>"


class TestSpreadsheetTextFidelity:
    """Cell text reaches the output whichever way the workbook stores it.

    A t="inlineStr" cell keeps its text in <is>, not <v>; a Strict Open XML
    workbook uses the purl.oclc.org namespace. Numbers came through either
    way, so both gaps lost text silently."""

    def test_inline_string_is_read_in_cell_order(self):
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="inlineStr"><is><t>Coffee</t></is></c>'
                    '<c r="B1"><v>42</v></c>'
                )
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "Coffee 42"

    def test_inline_rich_text_runs_concatenate(self):
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="inlineStr"><is><r><t>Cof</t></r>'
                    '<r><rPr><b/></rPr><t xml:space="preserve">fee </t></r></is></c>'
                    '<c r="B1"><v>42</v></c>'
                )
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "Coffee  42"

    @pytest.mark.parametrize("kind", ["inline", "shared"])
    def test_phonetic_guide_is_not_cell_text(self, kind):
        """<rPh> holds a reading aid (furigana), which is not the cell's text.
        Inline and shared strings share one reader, so both agree."""
        rich = '<r><t>東京</t></r><rPh sb="0" eb="2"><t>とうきょう</t></rPh>'
        if kind == "inline":
            members = {
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    f'<c r="A1" t="inlineStr"><is>{rich}</is></c>'
                )
            }
        else:
            members = {
                "xl/worksheets/sheet1.xml": _shared_string_sheet("0"),
                "xl/sharedStrings.xml": _sst(rich),
            }
        assert extract_office_xml_text(_xlsx(**members), XLSX_MIME) == "東京"

    def test_shared_and_inline_strings_keep_document_order(self):
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="s"><v>0</v></c>'
                    '<c r="B1" t="inlineStr"><is><t>two</t></is></c>'
                    '<c r="C1"><v>3</v></c>'
                    '<c r="D1" t="s"><v>1</v></c>'
                ),
                "xl/sharedStrings.xml": _sst("<t>one</t>", "<t>four</t>"),
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "one two 3 four"

    def test_empty_inline_string_is_an_empty_cell(self):
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="inlineStr"><is/></c>'
                    '<c r="B1" t="inlineStr"/>'
                    '<c r="C1"><v>42</v></c>'
                )
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "42"

    def test_inline_string_cell_without_is_falls_back_to_v(self):
        """Some writers put inlineStr text in <v>; keep reading it there."""
        data = _xlsx(
            **{
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="inlineStr"><v>Coffee</v></c>'
                )
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "Coffee"

    def test_strict_workbook_with_shared_strings(self):
        data = _zip(
            **{
                "xl/workbook.xml": f"<workbook {STRICT_SHEET_NS}/>",
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c>',
                    ns=STRICT_SHEET_NS,
                ),
                "xl/sharedStrings.xml": _sst("<t>Coffee</t>", ns=STRICT_SHEET_NS),
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "Coffee 42"

    def test_strict_workbook_with_inline_strings(self):
        data = _zip(
            **{
                "xl/workbook.xml": f"<workbook {STRICT_SHEET_NS}/>",
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="inlineStr"><is><t>Coffee</t></is></c>'
                    '<c r="B1"><v>42</v></c>',
                    ns=STRICT_SHEET_NS,
                ),
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "Coffee 42"

    def test_strict_iso_date_cell_passes_through_unconverted(self):
        data = _zip(
            **{
                "xl/workbook.xml": f"<workbook {STRICT_SHEET_NS}/>",
                "xl/worksheets/sheet1.xml": _cells_sheet(
                    '<c r="A1" t="d"><v>2024-01-31T00:00:00</v></c>',
                    ns=STRICT_SHEET_NS,
                ),
            }
        )
        assert extract_office_xml_text(data, XLSX_MIME) == "2024-01-31T00:00:00"

    def test_inline_string_text_counts_against_the_extracted_text_budget(
        self, monkeypatch
    ):
        """Inline text is charged like any other cell text.

        Inline text sits in the worksheet XML, so it alone can never outgrow the
        expansion budget first; shared strings bring the text close to the limit
        and the inline cell must be what crosses it.
        """
        monkeypatch.setenv(ENV, "20000")
        shared = "".join(f'<c r="A{row}" t="s"><v>0</v></c>' for row in range(1, 151))
        inline = f'<c r="B1" t="inlineStr"><is><t>{"B" * 6_000}</t></is></c>'
        members = {
            "xl/workbook.xml": "<workbook/>",
            "xl/sharedStrings.xml": _sst(f"<t>{'A' * 100}</t>"),
        }
        without_inline = _deflated(
            **members, **{"xl/worksheets/sheet1.xml": _cells_sheet(shared)}
        )
        with_inline = {
            **members,
            "xl/worksheets/sheet1.xml": _cells_sheet(shared + inline),
        }
        # The XML fits the expansion budget, and without the inline cell so
        # does the text: only the inline text can push it over.
        assert sum(len(xml.encode()) for xml in with_inline.values()) < 20_000
        assert extract_office_xml_text(without_inline, XLSX_MIME)

        with pytest.raises(OfficeXmlTooLargeError, match="extracted text") as exc:
            extract_office_xml_text(_deflated(**with_inline), XLSX_MIME)
        assert "extracting text from xl/worksheets/sheet1.xml" in str(exc.value)

    def test_strict_workbook_parts_are_charged_to_the_expansion_budget(
        self, monkeypatch
    ):
        """Every Strict part read is counted: text at exactly the total, and a
        refusal one byte below it."""
        members = {
            "xl/workbook.xml": f"<workbook {STRICT_SHEET_NS}/>",
            "xl/sharedStrings.xml": _sst("<t>Coffee</t>", ns=STRICT_SHEET_NS),
            "xl/worksheets/sheet1.xml": _cells_sheet(
                '<c r="A1" t="s"><v>0</v></c>'
                '<c r="B1" t="inlineStr"><is><t>Tea</t></is></c>',
                ns=STRICT_SHEET_NS,
            ),
        }
        total = sum(len(xml.encode()) for xml in members.values())
        data = _deflated(**members)

        monkeypatch.setenv(ENV, str(total))
        assert extract_office_xml_text(data, XLSX_MIME) == "Coffee Tea"

        monkeypatch.setenv(ENV, str(total - 1))
        with pytest.raises(OfficeXmlTooLargeError, match="sheet1.xml"):
            extract_office_xml_text(data, XLSX_MIME)
