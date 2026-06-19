import json
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

from core.utils import UserInputError
from gdocs import docs_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class TestHeaderFooterGuardrails:
    @pytest.mark.asyncio
    async def test_update_doc_headers_footers_handles_empty_created_segment(self):
        service = Mock()
        docs_api = service.documents.return_value

        docs_api.get.return_value.execute.side_effect = [
            {"headers": {}, "footers": {}},
            {"headers": {"hdr-123": {"content": []}}, "footers": {}},
        ]
        docs_api.batchUpdate.return_value.execute.side_effect = [
            {"replies": [{"createHeader": {"headerId": "hdr-123"}}]},
            {"replies": [{}]},
        ]

        result = await _unwrap(docs_tools.update_doc_headers_footers)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            section_type="header",
            content="Board Draft",
        )

        assert "Updated header content" in result
        assert docs_tools.HEADER_FOOTER_RUNTIME_CANARY in result

        write_call = docs_api.batchUpdate.call_args_list[1]
        request = write_call.kwargs["body"]["requests"][0]["insertText"]
        assert request["text"] == "Board Draft"
        assert request["endOfSegmentLocation"] == {"segmentId": "hdr-123"}

    @pytest.mark.asyncio
    async def test_update_doc_headers_footers_skips_empty_range_delete(self):
        service = Mock()
        docs_api = service.documents.return_value

        docs_api.get.return_value.execute.return_value = {
            "documentStyle": {"defaultHeaderId": "hdr-123"},
            "headers": {
                "hdr-123": {
                    "content": [
                        {
                            "startIndex": 0,
                            "endIndex": 1,
                            "paragraph": {"elements": []},
                        }
                    ]
                }
            },
            "footers": {},
            "body": {"content": []},
        }
        docs_api.batchUpdate.return_value.execute.return_value = {"replies": [{}]}

        result = await _unwrap(docs_tools.update_doc_headers_footers)(
            service=service,
            user_google_email="user@example.com",
            document_id="j" * 25,
            section_type="header",
            content="Header Text",
        )

        assert "Updated header content" in result

        requests = docs_api.batchUpdate.call_args.kwargs["body"]["requests"]
        assert len(requests) == 1
        assert "insertText" in requests[0]
        assert requests[0]["insertText"]["endOfSegmentLocation"] == {
            "segmentId": "hdr-123"
        }

    @pytest.mark.asyncio
    async def test_batch_update_doc_duplicate_header_creation_returns_guidance(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = {
            "documentStyle": {"defaultHeaderId": "hdr-123"},
            "body": {"content": []},
        }

        result = await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="b" * 25,
            operations=[{"type": "create_header_footer", "section_type": "header"}],
        )

        assert "update_doc_headers_footers" in result
        assert "already exists" in result

    @pytest.mark.asyncio
    async def test_batch_update_doc_invalid_section_break_index_returns_guidance(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = {
            "documentStyle": {},
            "body": {
                "content": [
                    {
                        "startIndex": 25,
                        "endIndex": 26,
                        "sectionBreak": {"sectionStyle": {}},
                    }
                ]
            },
        }

        result = await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="g" * 25,
            operations=[
                {
                    "type": "create_header_footer",
                    "section_type": "header",
                    "section_break_index": 1,
                }
            ],
        )

        assert "section_break_index must match an existing section break" in result
        assert "Available section break indices: [25]" in result

    @pytest.mark.asyncio
    async def test_modify_doc_text_invalid_segment_id_returns_guidance(self):
        service = Mock()
        service.documents.return_value.batchUpdate.return_value.execute.side_effect = (
            HttpError(
                resp=Mock(status=400),
                content=(
                    b'{"error":{"message":"Invalid requests[0].insertText: Segment '
                    b"with ID kix.header was not found. If a segment ID is "
                    b'provided, it must be a header, footer or footnote ID."}}'
                ),
            )
        )

        with pytest.raises(UserInputError) as exc_info:
            await _unwrap(docs_tools.modify_doc_text)(
                service=service,
                user_google_email="user@example.com",
                document_id="c" * 25,
                start_index=1,
                text="Header text",
                segment_id="kix.header",
                end_of_segment=True,
            )

        message = str(exc_info.value)
        assert "inspect_doc_structure" in message
        assert "do not guess IDs such as 'kix.header'" in message
        assert "update_doc_headers_footers" in message

    @pytest.mark.asyncio
    async def test_inspect_doc_structure_exposes_header_footer_segment_ids(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = {
            "title": "Board Draft",
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 12,
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Hello world"}}]
                        },
                    }
                ]
            },
            "headers": {
                "hdr-123": {
                    "content": [
                        {
                            "startIndex": 0,
                            "endIndex": 10,
                            "paragraph": {
                                "elements": [{"textRun": {"content": "Board Draft"}}]
                            },
                        }
                    ]
                }
            },
            "footers": {
                "ftr-456": {
                    "content": [
                        {
                            "startIndex": 0,
                            "endIndex": 8,
                            "paragraph": {
                                "elements": [{"textRun": {"content": "Footer"}}]
                            },
                        }
                    ]
                }
            },
            "tabs": [],
            "namedRanges": {},
        }

        result = await _unwrap(docs_tools.inspect_doc_structure)(
            service=service,
            user_google_email="user@example.com",
            document_id="d" * 25,
            detailed=True,
        )

        payload = result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0]
        parsed = json.loads(payload)

        assert parsed["headers"][0]["segment_id"] == "hdr-123"
        assert parsed["headers"][0]["content_preview"] == "Board Draft"
        assert parsed["footers"][0]["segment_id"] == "ftr-456"
        assert parsed["footers"][0]["content_preview"] == "Footer"

    @pytest.mark.asyncio
    async def test_update_doc_headers_footers_uses_style_derived_segment_id(self):
        service = Mock()
        docs_api = service.documents.return_value

        docs_api.get.return_value.execute.return_value = {
            "documentStyle": {"defaultHeaderId": "hdr-style-1"},
            "headers": {},
            "footers": {},
            "body": {"content": []},
        }
        docs_api.batchUpdate.return_value.execute.return_value = {"replies": [{}]}

        result = await _unwrap(docs_tools.update_doc_headers_footers)(
            service=service,
            user_google_email="user@example.com",
            document_id="e" * 25,
            section_type="header",
            content="Internal Use Only",
        )

        assert "Updated header content" in result

        request = docs_api.batchUpdate.call_args.kwargs["body"]["requests"][0][
            "insertText"
        ]
        assert request["endOfSegmentLocation"] == {"segmentId": "hdr-style-1"}

    @pytest.mark.asyncio
    async def test_update_doc_headers_footers_uses_tab_id_for_tabbed_docs(self):
        service = Mock()
        docs_api = service.documents.return_value

        docs_api.get.return_value.execute.return_value = {
            "title": "Tabbed Doc",
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0"},
                    "documentTab": {
                        "body": {"content": []},
                        "headers": {},
                        "footers": {},
                        "documentStyle": {"defaultHeaderId": "hdr-tab-1"},
                    },
                }
            ],
        }
        docs_api.batchUpdate.return_value.execute.return_value = {"replies": [{}]}

        result = await _unwrap(docs_tools.update_doc_headers_footers)(
            service=service,
            user_google_email="user@example.com",
            document_id="h" * 25,
            section_type="header",
            content="Tabbed Header",
        )

        assert "Updated header content" in result

        request = docs_api.batchUpdate.call_args.kwargs["body"]["requests"][0][
            "insertText"
        ]
        assert request["endOfSegmentLocation"] == {
            "segmentId": "hdr-tab-1",
            "tabId": "t.0",
        }

    @pytest.mark.asyncio
    async def test_inspect_doc_structure_exposes_style_derived_segment_ids(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = {
            "title": "Board Draft",
            "body": {"content": []},
            "headers": {},
            "footers": {},
            "documentStyle": {
                "defaultHeaderId": "hdr-style-1",
                "defaultFooterId": "ftr-style-1",
            },
            "tabs": [],
            "namedRanges": {},
        }

        result = await _unwrap(docs_tools.inspect_doc_structure)(
            service=service,
            user_google_email="user@example.com",
            document_id="f" * 25,
            detailed=True,
        )

        payload = result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0]
        parsed = json.loads(payload)

        assert parsed["headers"][0]["segment_id"] == "hdr-style-1"
        assert parsed["headers"][0]["source"] == "documentStyle.defaultHeaderId"
        assert parsed["footers"][0]["segment_id"] == "ftr-style-1"
        assert parsed["footers"][0]["source"] == "documentStyle.defaultFooterId"

    @pytest.mark.asyncio
    async def test_inspect_doc_structure_uses_document_tab_headers_and_footers(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = {
            "title": "Tabbed Doc",
            "body": {"content": []},
            "headers": {},
            "footers": {},
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0"},
                    "documentTab": {
                        "body": {"content": []},
                        "headers": {
                            "hdr-tab-1": {
                                "content": [
                                    {
                                        "startIndex": 0,
                                        "endIndex": 6,
                                        "paragraph": {
                                            "elements": [
                                                {"textRun": {"content": "Hello"}}
                                            ]
                                        },
                                    }
                                ]
                            }
                        },
                        "footers": {
                            "ftr-tab-1": {
                                "content": [
                                    {
                                        "startIndex": 0,
                                        "endIndex": 7,
                                        "paragraph": {
                                            "elements": [
                                                {"textRun": {"content": "Footer"}}
                                            ]
                                        },
                                    }
                                ]
                            }
                        },
                        "documentStyle": {},
                    },
                }
            ],
            "namedRanges": {},
        }

        result = await _unwrap(docs_tools.inspect_doc_structure)(
            service=service,
            user_google_email="user@example.com",
            document_id="i" * 25,
            detailed=True,
        )

        payload = result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0]
        parsed = json.loads(payload)

        assert parsed["headers"][0]["segment_id"] == "hdr-tab-1"
        assert parsed["headers"][0]["content_preview"] == "Hello"
        assert parsed["footers"][0]["segment_id"] == "ftr-tab-1"
        assert parsed["footers"][0]["content_preview"] == "Footer"

    @pytest.mark.asyncio
    async def test_debug_docs_runtime_info_reports_canary_and_paths(self):
        service = Mock()

        result = await _unwrap(docs_tools.debug_docs_runtime_info)(
            service=service,
            user_google_email="user@example.com",
        )

        parsed = json.loads(result)
        assert parsed["runtime_canary"] == docs_tools.HEADER_FOOTER_RUNTIME_CANARY
        assert parsed["docs_tools_file"].endswith("gdocs/docs_tools.py")
        assert parsed["header_footer_manager_file"].endswith(
            "gdocs/managers/header_footer_manager.py"
        )
