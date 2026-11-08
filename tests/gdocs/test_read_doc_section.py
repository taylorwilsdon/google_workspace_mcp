"""Tests for reading one heading section of a doc with the Docs read tools."""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gdocs import docs_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _para(start, text, style="NORMAL_TEXT"):
    """Build a body paragraph element; text must end with a newline."""
    end = start + len(text)
    return {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": style},
            "elements": [
                {"startIndex": start, "endIndex": end, "textRun": {"content": text}}
            ],
        },
    }


def _body(*paragraphs):
    """Lay out (text, style) pairs as consecutive body paragraphs from index 1."""
    content = [{"endIndex": 1, "sectionBreak": {}}]
    start = 1
    for text, style in paragraphs:
        content.append(_para(start, text, style))
        start += len(text)
    return {"content": content}


def _tab(tab_id, body, **extra):
    return {
        "tabProperties": {"tabId": tab_id, "title": tab_id},
        "documentTab": {"body": body, **extra},
    }


# Indices: Intro 1, Status 7, Old body 14, Sub 23, sub body 27, Details 36, Tail 44
BODY = _body(
    ("Intro\n", "HEADING_1"),
    ("Status\n", "HEADING_2"),
    ("Old body\n", "NORMAL_TEXT"),
    ("Sub\n", "HEADING_3"),
    ("sub body\n", "NORMAL_TEXT"),
    ("Details\n", "HEADING_2"),
    ("Tail\n", "NORMAL_TEXT"),
)


def _doc(body=BODY, **extra):
    return {"title": "Log", "tabs": [_tab("t.0", body, **extra)]}


def _drive_service(comments=()):
    service = Mock()
    service.files.return_value.get.return_value.execute = Mock(
        return_value={
            "id": "doc123",
            "name": "Log",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/doc123/edit",
        }
    )
    service.comments.return_value.list.return_value.execute = Mock(
        return_value={"comments": list(comments)}
    )
    return service


def _docs_service(doc):
    service = Mock()
    service.documents.return_value.get.return_value.execute = Mock(return_value=doc)
    return service


async def _content(doc, document_id="doc123", **kwargs):
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=_drive_service(),
        docs_service=_docs_service(doc),
        user_google_email="user@example.com",
        document_id=document_id,
        **kwargs,
    )
    return result


async def _markdown(doc, comments=(), document_id="doc123", **kwargs):
    return await _unwrap(docs_tools.get_doc_as_markdown)(
        drive_service=_drive_service(comments),
        docs_service=_docs_service(doc),
        user_google_email="user@example.com",
        document_id=document_id,
        **kwargs,
    )


def _body_text(result):
    return result.split("--- CONTENT ---\n", 1)[1]


@pytest.mark.asyncio
async def test_content_section_includes_subheadings_until_next_same_level():
    result = await _content(_doc(), heading="Status")
    assert _body_text(result) == "Status\nOld body\nSub\nsub body\n"
    assert "section under heading 'Status', starting at document index 7." in result


@pytest.mark.asyncio
async def test_markdown_section_includes_subheadings_until_next_same_level():
    result = await _markdown(_doc(), heading="status", include_comments=False)
    assert result == "## Status\n\nOld body\n\n### Sub\n\nsub body\n"


@pytest.mark.asyncio
async def test_last_section_runs_to_end_of_document():
    assert _body_text(await _content(_doc(), heading="Details")) == "Details\nTail\n"
    result = await _markdown(_doc(), heading="Details", include_comments=False)
    assert result == "## Details\n\nTail\n"


@pytest.mark.asyncio
async def test_higher_level_heading_ends_a_nested_section():
    result = await _content(_doc(), heading="Sub")
    assert _body_text(result) == "Sub\nsub body\n"


@pytest.mark.asyncio
async def test_without_heading_output_is_unchanged():
    expected = "Intro\nStatus\nOld body\nSub\nsub body\nDetails\nTail\n"
    assert _body_text(await _content(_doc())).endswith(expected)
    markdown = await _markdown(_doc(), include_comments=False)
    assert markdown.startswith("# Intro\n\n## Status\n")
    assert markdown.endswith("## Details\n\nTail\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("read", [_content, _markdown])
async def test_unknown_heading_lists_available_headings(read):
    with pytest.raises(UserInputError, match="No heading matching 'Budget'") as exc:
        await read(_doc(), heading="Budget")
    assert "Available headings: 'Intro', 'Status', 'Sub', 'Details'" in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_heading_caps_the_listed_headings():
    doc = _doc(_body(*[(f"H{n}\n", "HEADING_2") for n in range(60)]))
    with pytest.raises(UserInputError) as exc:
        await _content(doc, heading="Budget")
    message = str(exc.value)
    assert "'H49' and 10 more." in message
    assert "'H50'" not in message


@pytest.mark.asyncio
async def test_document_without_headings_says_so():
    doc = _doc(_body(("Just text\n", "NORMAL_TEXT")))
    with pytest.raises(UserInputError, match="has no headings"):
        await _content(doc, heading="Intro")


@pytest.mark.asyncio
@pytest.mark.parametrize("heading", ["", "   "])
async def test_blank_heading_is_rejected(heading):
    doc = _doc(_body(("\n", "HEADING_2"), ("orphan\n", "NORMAL_TEXT")))
    with pytest.raises(UserInputError, match="'heading' must not be empty"):
        await _markdown(doc, heading=heading)


@pytest.mark.asyncio
async def test_heading_level_out_of_range_is_rejected():
    with pytest.raises(UserInputError, match="between 1 and 6"):
        await _content(_doc(), heading="Status", heading_level=7)


@pytest.mark.asyncio
async def test_duplicate_headings_need_heading_level():
    doc = _doc(
        _body(
            ("Notes\n", "HEADING_1"),
            ("top\n", "NORMAL_TEXT"),
            ("Notes\n", "HEADING_2"),
            ("nested\n", "NORMAL_TEXT"),
        )
    )
    with pytest.raises(UserInputError, match="matches 2 headings"):
        await _content(doc, heading="Notes")
    result = await _content(doc, heading="Notes", heading_level=2)
    assert _body_text(result) == "Notes\nnested\n"


@pytest.mark.asyncio
async def test_unicode_and_padded_headings_match():
    doc = _doc(
        _body(
            ("  Résumé 🚀 \n", "HEADING_1"),
            ("emoji body\n", "NORMAL_TEXT"),
            ("Ünïcode \n", "HEADING_1"),
            ("other\n", "NORMAL_TEXT"),
        )
    )
    result = await _content(doc, heading="résumé 🚀")
    assert _body_text(result) == "  Résumé 🚀 \nemoji body\n"
    result = await _markdown(doc, heading="ÜNÏCODE", include_comments=False)
    assert "other" in result and "emoji body" not in result


@pytest.mark.asyncio
async def test_multi_tab_doc_reads_section_of_named_tab():
    doc = {
        "tabs": [
            _tab("t.a", _body(("Status\n", "HEADING_2"), ("tab a\n", "NORMAL_TEXT"))),
            _tab("t.b", BODY),
        ]
    }
    with pytest.raises(UserInputError, match="tab_id is required"):
        await _content(doc, heading="Status")
    result = await _content(doc, heading="Status", tab_id="t.b")
    assert _body_text(result) == "Status\nOld body\nSub\nsub body\n"
    result = await _markdown(
        doc, heading="Status", tab_id="t.a", include_comments=False
    )
    assert result == "## Status\n\ntab a\n"


@pytest.mark.asyncio
async def test_url_selected_tab_is_named_in_section_notice():
    doc = {
        "tabs": [
            _tab("t.a", _body(("Status\n", "HEADING_2"), ("tab a\n", "NORMAL_TEXT"))),
            _tab("t.b", BODY),
        ]
    }
    url = "https://docs.google.com/document/d/doc123/edit?tab=t.b"
    tab_notice = "Showing only tab 't.b' (t.b) from the URL"
    result = await _content(doc, document_id=url, heading="Status")
    header, body = result.split("--- CONTENT ---\n", 1)
    assert tab_notice in header
    assert "section under heading 'Status', starting at document index 7." in header
    assert body == "Status\nOld body\nSub\nsub body\n"
    result = await _markdown(
        doc, document_id=url, heading="Status", include_comments=False
    )
    assert result.startswith(f"*{tab_notice}")
    assert result.endswith("## Status\n\nOld body\n\n### Sub\n\nsub body\n")


@pytest.mark.asyncio
async def test_empty_tab_id_reads_the_only_tab():
    result = await _content(_doc(), heading="Details", tab_id="")
    assert _body_text(result) == "Details\nTail\n"


@pytest.mark.asyncio
async def test_preserve_context_section_omits_headers_and_footers():
    doc = _doc(
        headers={"h.1": {"content": [_para(0, "Header text\n")]}},
        footers={"f.1": {"content": [_para(0, "Footer text\n")]}},
    )
    result = await _content(doc, heading="Details", preserve_context=True)
    assert _body_text(result) == "Details\nTail\n"


@pytest.mark.asyncio
async def test_preserve_context_section_keeps_only_its_positioned_objects():
    body = _body(
        ("Intro\n", "HEADING_1"),
        ("far\n", "NORMAL_TEXT"),
        ("Status\n", "HEADING_1"),
        ("near\n", "NORMAL_TEXT"),
    )
    body["content"][2]["paragraph"]["positionedObjectIds"] = ["kix.far"]
    body["content"][4]["paragraph"]["positionedObjectIds"] = ["kix.near"]
    doc = _doc(body, positionedObjects={"kix.far": {}, "kix.near": {}})
    result = await _content(doc, heading="Status", preserve_context=True)
    assert "kix.near" in result
    assert "kix.far" not in result


@pytest.mark.asyncio
async def test_heading_on_office_file_is_rejected():
    drive = _drive_service()
    drive.files.return_value.get.return_value.execute.return_value["mimeType"] = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=_docs_service(_doc()),
        user_google_email="user@example.com",
        document_id="doc123",
        heading="Status",
    )
    assert result == "Error: 'heading' is only supported for native Google Docs."
    drive.files.return_value.get_media.assert_not_called()


@pytest.mark.asyncio
async def test_markdown_section_lists_comments_as_document_wide():
    comments = [
        {
            "id": "c1",
            "content": "Check this",
            "author": {"displayName": "Ana"},
            "quotedFileContent": {"value": "Old body"},
            "replies": [],
        }
    ]
    result = await _markdown(_doc(), comments=comments, heading="Status")
    assert "## Comments (entire document)" in result
    assert "[^c1]" not in result
