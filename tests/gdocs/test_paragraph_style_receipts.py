"""Standalone paragraph formatting reports bounded, best-effort write readbacks."""

from unittest.mock import Mock

import pytest

from gdocs import docs_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _paragraph(index, text, style="HEADING_1", bullet=False):
    paragraph = {
        "paragraphStyle": {"namedStyleType": style},
        "elements": [{"textRun": {"content": text}}],
    }
    if bullet:
        paragraph["bullet"] = {"listId": "list-1"}
    return {"startIndex": index, "endIndex": index + len(text), "paragraph": paragraph}


def _service(after, before=None, write_error=None):
    service = Mock()
    events = []

    def get(**kwargs):
        events.append(("get", kwargs))
        value = after if kwargs.get("includeTabsContent") else before
        if isinstance(value, Exception):
            return Mock(execute=Mock(side_effect=value))
        return Mock(execute=Mock(return_value=value or {"revisionId": "before"}))

    def write(**kwargs):
        events.append(("write", kwargs))
        return Mock(
            execute=Mock(side_effect=write_error, return_value={"replies": [{}]})
        )

    service.documents.return_value.get.side_effect = get
    service.documents.return_value.batchUpdate.side_effect = write
    return service, events


async def _update(service, **kwargs):
    return await _unwrap(docs_tools.update_paragraph_style)(
        service=service,
        user_google_email="test@example.test",
        document_id="synthetic_document_1234567890",
        start_index=1,
        end_index=kwargs.pop("end_index", 10),
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tab_id,segment_id",
    [
        (None, None),
        ("child", None),
        ("child", "header"),
        ("child", "footer"),
        ("child", "note"),
    ],
)
async def test_receipt_reads_only_selected_coordinate_space(tab_id, segment_id):
    selected = {"content": [_paragraph(1, "Stored heading\n", bullet=True)]}
    other = {"content": [_paragraph(1, "Wrong target\n")]}
    child = {
        "body": selected,
        "headers": {"header": selected},
        "footers": {"footer": selected},
        "footnotes": {"note": selected},
    }
    after = {
        "revisionId": "after",
        "tabs": [
            {
                "tabProperties": {"tabId": "first"},
                "documentTab": {"body": selected if tab_id is None else other},
                "childTabs": [
                    {"tabProperties": {"tabId": "child"}, "documentTab": child}
                ],
            }
        ],
    }
    service, events = _service(after)
    result = await _update(
        service, heading_level=1, tab_id=tab_id, segment_id=segment_id
    )
    assert result.startswith("Applied paragraph formatting (HEADING_1) to range 1-10")
    assert "Revision: before -> after." in result
    assert (
        'Requested range after edit:\n  [1-16] HEADING_1, in list: "Stored heading"'
        in result
    )
    assert "Wrong target" not in result
    assert [event[0] for event in events] == ["get", "write", "get"]
    assert events[0][1]["fields"] == "revisionId"
    assert events[2][1] == {
        "documentId": "synthetic_document_1234567890",
        "includeTabsContent": True,
        "fields": "revisionId,tabs",
    }
    expected_range = {"startIndex": 1, "endIndex": 10}
    if tab_id:
        expected_range["tabId"] = tab_id
    if segment_id:
        expected_range["segmentId"] = segment_id
    assert events[1][1]["body"] == {
        "requests": [
            {
                "updateParagraphStyle": {
                    "range": expected_range,
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields": "namedStyleType",
                }
            }
        ]
    }


@pytest.mark.asyncio
async def test_receipt_is_bounded_and_nested_list_reuses_pre_read():
    paragraphs = [_paragraph(1 + i * 101, "x" * 100 + "\n") for i in range(21)]
    before = {"revisionId": "before", "body": {"content": paragraphs}}
    after = {
        "revisionId": "after",
        "tabs": [{"documentTab": {"body": {"content": paragraphs}}}],
    }
    service, events = _service(after, before)
    result = await _update(
        service, end_index=2122, list_type="ORDERED", list_nesting_level=1
    )
    assert [event[0] for event in events] == ["get", "write", "get"]
    assert (
        events[0][1]["fields"]
        == "revisionId,body/content(startIndex,endIndex,paragraph)"
    )
    assert "Revision: before -> after." in result
    assert result.count('HEADING_1: "') == 20
    assert '"' + "x" * 80 + '"' in result
    assert "x" * 81 not in result
    assert "index shifts from list edits are not tracked" in result
    assert len(events[1][1]["body"]["requests"]) == 22


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_read", ["before", "after", "both", "missing_target", "no_paragraphs"]
)
async def test_receipt_failure_never_hides_successful_write(failed_read):
    after = {
        "revisionId": "after",
        "tabs": [{"documentTab": {"body": {"content": [_paragraph(1, "Stored\n")]}}}],
    }
    before = (
        RuntimeError("private upstream failure")
        if failed_read in ("before", "both")
        else None
    )
    if failed_read in ("after", "both"):
        after = RuntimeError("private upstream failure")
    elif failed_read == "missing_target":
        after = {"revisionId": "after", "tabs": []}
    elif failed_read == "no_paragraphs":
        after["tabs"][0]["documentTab"]["body"]["content"] = []
    service, events = _service(after, before)
    result = await _update(
        service,
        list_type="NONE",
        tab_id="missing" if failed_read == "missing_target" else None,
    )
    assert result.startswith("Applied paragraph formatting (none list)")
    assert [event[0] for event in events] == ["get", "write", "get"]
    assert "private upstream failure" not in result
    if failed_read in ("after", "both", "missing_target"):
        assert "Verification unavailable: the write succeeded" in result
    if failed_read in ("before", "both"):
        assert "Revision: unknown ->" in result
    if failed_read == "no_paragraphs":
        assert "No top-level paragraphs were returned" in result


@pytest.mark.asyncio
async def test_write_failure_does_not_fetch_or_report_a_receipt():
    service, events = _service({}, write_error=RuntimeError("write failed"))
    with pytest.raises(RuntimeError, match="write failed"):
        await _update(service, heading_level=1)
    assert [event[0] for event in events] == ["get", "write"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"heading_level": 8}])
async def test_invalid_or_noop_does_not_fetch_receipts(kwargs):
    service, events = _service({})
    result = await _update(service, **kwargs)
    assert result.startswith(("Error:", "No paragraph style"))
    assert events == []
