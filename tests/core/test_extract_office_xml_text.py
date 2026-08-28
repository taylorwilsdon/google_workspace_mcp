"""Tests for extract_office_xml_text in core/utils.py.

Focus is text FIDELITY for Word documents: the extracted string has to be the
string a human reads, because callers search it. A search that silently misses
is worse than no search — it produces a confident "not present" that is wrong.
"""

import io
import zipfile

from core.utils import extract_office_xml_text

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
