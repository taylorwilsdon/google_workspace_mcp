"""Markdown to Google Docs API batchUpdate request converter.

Parses CommonMark markdown via markdown-it-py (commonmark preset) and emits
a list of Docs API request dicts that, when applied in order, render the
markdown into a document or a specific tab within a document.

Supported constructs - headings H1-H6, paragraphs with inline bold/italic/
code/links, ordered and unordered lists, fenced code blocks, blockquotes,
horizontal rules, image alt text linked to the image URL, and GFM pipe
tables (rendered as native Docs tables with bold header rows). Other
GFM-only features (strikethrough, task lists, autolinks) are not enabled;
extend the parser config below if they become needed.

Primary entry point - markdown_to_docs_requests(markdown_text, tab_id=None).
"""

from __future__ import annotations

from typing import Optional

from markdown_it import MarkdownIt

from gdocs.docs_helpers import (
    create_delete_bullet_list_request,
    create_delete_range_request,
    create_insert_text_request,
)

_NORMAL_TEXT = {"namedStyleType": "NORMAL_TEXT"}
_BLOCK_GAP = {"spaceBelow": {"magnitude": 12, "unit": "PT"}}
_DOCS_MAX_TABLE_ROWS = 1000
_DOCS_MAX_TABLE_COLS = 20


def markdown_to_docs_requests(
    markdown_text: str,
    tab_id: Optional[str] = None,
    start_index: int = 1,
) -> list[dict]:
    """Convert markdown to a list of Docs API batchUpdate request dicts.

    Args:
        markdown_text - the markdown source
        tab_id - optional tab ID; when provided, every range targets this tab
        start_index - document index at which content insertion begins

    Returns:
        Ordered list of request dicts. Empty list for empty input.
    """
    if not markdown_text.strip():
        return []

    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(markdown_text)

    requests: list[dict] = []
    _emit_requests(tokens, requests, tab_id, start_index)
    return requests


def _utf16_len(text: str) -> int:
    """Return the length of text in UTF-16 code units, the Docs API index unit."""
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _emit_requests(tokens, requests, tab_id, start_index):
    """Walk markdown-it tokens and append Docs API requests.

    Maintains a running `cursor` that represents the current insertion point
    in the document. Each insertText advances cursor by _utf16_len(text).
    """
    cursor = [start_index]  # mutable via list so helpers can advance it

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok.type == "heading_open":
            level = int(tok.tag[1])  # 'h1' -> 1
            inline_tok = tokens[i + 1]
            text, inline_styles = _render_inline_with_styles(
                inline_tok.children or [], cursor[0], tab_id
            )
            text += "\n"
            range_start = cursor[0]
            requests.append(_build_insert_text(cursor[0], text, tab_id))
            cursor[0] += _utf16_len(text)
            requests.append(
                _build_paragraph_style(
                    range_start,
                    cursor[0],
                    {"namedStyleType": f"HEADING_{level}"},
                    tab_id,
                )
            )
            requests.extend(inline_styles)
            i += 3
            continue

        if tok.type in ("bullet_list_open", "ordered_list_open"):
            preset = (
                "BULLET_DISC_CIRCLE_SQUARE"
                if tok.type == "bullet_list_open"
                else "NUMBERED_DECIMAL_ALPHA_ROMAN"
            )
            list_start = cursor[0]
            # Find the matching closing token
            close_type = tok.type.replace("_open", "_close")
            depth = 1
            j = i + 1
            while j < len(tokens) and depth > 0:
                if tokens[j].type == tok.type:
                    depth += 1
                elif tokens[j].type == close_type:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            # Iterate items between i and j
            k = i + 1
            while k < j:
                item = tokens[k]
                if item.type == "table_open":
                    row_cells, k = _collect_table_cells(tokens, k)
                    _emit_table_rows_as_text(row_cells, requests, cursor, tab_id)
                elif item.type == "list_item_open":
                    # Inner structure typically - list_item_open, paragraph_open, inline, paragraph_close, list_item_close
                    # Find the inline token within this list_item
                    if k + 2 < j and tokens[k + 2].type == "inline":
                        inline_tok = tokens[k + 2]
                        text, inline_styles = _render_inline_with_styles(
                            inline_tok.children or [], cursor[0], tab_id
                        )
                        text += "\n"
                        requests.append(_build_insert_text(cursor[0], text, tab_id))
                        cursor[0] += _utf16_len(text)
                        requests.extend(inline_styles)
                k += 1
            list_end = cursor[0]
            requests.append(
                _build_paragraph_style(list_start, list_end, _NORMAL_TEXT, tab_id)
            )
            # One createParagraphBullets covering the full list range
            rng = {"startIndex": list_start, "endIndex": list_end}
            if tab_id:
                rng["tabId"] = tab_id
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": rng,
                        "bulletPreset": preset,
                    }
                }
            )
            _append_block_gap(requests, list_start, list_end, tab_id)
            i = j + 1
            continue

        if tok.type == "fence":
            content = tok.content
            start_idx = cursor[0]
            # Ensure exactly one trailing newline to end the code paragraph.
            text = content if content.endswith("\n") else content + "\n"
            requests.append(_build_insert_text(cursor[0], text, tab_id))
            cursor[0] += _utf16_len(text)
            requests.append(
                _build_paragraph_style(start_idx, cursor[0], _NORMAL_TEXT, tab_id)
            )
            # Style the code characters but not the paragraph-ending newline.
            code_end = cursor[0] - 1
            _append_text_style(
                requests,
                start_idx,
                code_end,
                {"weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}},
                "weightedFontFamily",
                tab_id,
            )
            _append_block_gap(requests, start_idx, cursor[0], tab_id)
            i += 1
            continue

        if tok.type == "blockquote_open":
            close_type = "blockquote_close"
            depth = 1
            j = i + 1
            while j < len(tokens) and depth > 0:
                if tokens[j].type == tok.type:
                    depth += 1
                elif tokens[j].type == close_type:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            quote_start = cursor[0]
            # Process paragraphs inside the blockquote
            k = i + 1
            while k < j:
                if tokens[k].type == "table_open":
                    row_cells, k = _collect_table_cells(tokens, k)
                    _emit_table_rows_as_text(row_cells, requests, cursor, tab_id)
                    k += 1
                    continue
                if (
                    tokens[k].type == "paragraph_open"
                    and k + 1 < j
                    and tokens[k + 1].type == "inline"
                ):
                    inline_tok = tokens[k + 1]
                    text, inline_styles = _render_inline_with_styles(
                        inline_tok.children or [], cursor[0], tab_id
                    )
                    text += "\n"
                    requests.append(_build_insert_text(cursor[0], text, tab_id))
                    cursor[0] += _utf16_len(text)
                    requests.extend(inline_styles)
                    k += 3
                    continue
                k += 1
            quote_end = cursor[0]
            # One request: Docs applies namedStyleType before indentStart.
            requests.append(
                _build_paragraph_style(
                    quote_start,
                    quote_end,
                    {
                        **_NORMAL_TEXT,
                        "indentStart": {"magnitude": 36, "unit": "PT"},
                    },
                    tab_id,
                )
            )
            _append_block_gap(requests, quote_start, quote_end, tab_id)
            i = j + 1
            continue

        if tok.type == "hr":
            _append_spacer(requests, cursor, tab_id)
            i += 1
            continue

        if tok.type == "table_open":
            row_cells, i = _collect_table_cells(tokens, i)
            _emit_table(row_cells, requests, cursor, tab_id)
            i += 1
            continue

        if tok.type == "paragraph_open":
            # paragraph_open is followed by inline (children), then paragraph_close
            inline_tok = tokens[i + 1]
            text, inline_styles = _render_inline_with_styles(
                inline_tok.children or [], cursor[0], tab_id
            )
            text += "\n"
            range_start = cursor[0]
            requests.append(_build_insert_text(cursor[0], text, tab_id))
            cursor[0] += _utf16_len(text)
            requests.append(
                _build_paragraph_style(range_start, cursor[0], _NORMAL_TEXT, tab_id)
            )
            requests.extend(inline_styles)
            _append_block_gap(requests, range_start, cursor[0], tab_id)
            i += 3  # skip paragraph_open, inline, paragraph_close
            continue

        i += 1


def _collect_table_cells(tokens, start):
    """Return (rows of cell inline children, index of the matching table_close)."""
    rows: list[list] = []
    k = start
    while tokens[k].type != "table_close":
        if tokens[k].type == "tr_open":
            rows.append([])
        elif tokens[k].type == "inline":
            rows[-1].append(tokens[k].children or [])
        k += 1
    return rows, k


def _emit_table_rows_as_text(row_cells, requests, cursor, tab_id):
    """Emit each table row as one plain-text paragraph with cells joined by ' | '."""
    for row in row_cells:
        cells = (_render_inline_with_styles(cell, 0, tab_id)[0] for cell in row)
        line = " | ".join(cells) + "\n"
        requests.append(_build_insert_text(cursor[0], line, tab_id))
        cursor[0] += _utf16_len(line)


def _emit_table(row_cells, requests, cursor, tab_id):
    """Emit insertTable plus cell fills for one table and advance cursor past it.

    An empty R x C table inserted at L keeps a newline paragraph at L, puts
    cell (r, c)'s paragraph at L + 4 + r * (1 + 2C) + 2c and ends at
    L + 3 + R * (1 + 2C); cells fill last to first so those indexes hold.
    """
    rows, cols = len(row_cells), len(row_cells[0])
    start = cursor[0]
    if rows > _DOCS_MAX_TABLE_ROWS or cols > _DOCS_MAX_TABLE_COLS:
        _emit_table_rows_as_text(row_cells, requests, cursor, tab_id)
        requests.append(_build_paragraph_style(start, cursor[0], _NORMAL_TEXT, tab_id))
        return

    if requests and requests[-1] == _build_paragraph_style(
        start - 1, start, _BLOCK_GAP, tab_id
    ):
        requests.pop()
    requests.append(_build_insert_table(start, rows, cols, tab_id))
    requests.append(_build_paragraph_style(start, start + 1, _NORMAL_TEXT, tab_id))
    stride = 1 + 2 * cols
    cursor[0] = start + 3 + rows * stride
    for r in reversed(range(rows)):
        for c in reversed(range(cols)):
            index = start + 4 + r * stride + 2 * c
            text, styles = _render_inline_with_styles(row_cells[r][c], index, tab_id)
            if not text:
                continue
            end = index + _utf16_len(text)
            requests.append(_build_insert_text(index, text, tab_id))
            requests.extend(styles)
            if r == 0:
                _append_text_style(requests, index, end, {"bold": True}, "bold", tab_id)
            cursor[0] += end - index


def _build_insert_table(
    index: int, rows: int, cols: int, tab_id: Optional[str]
) -> dict:
    """Build an insertTable request dict, threading tab_id if provided."""
    location = {"index": index}
    if tab_id:
        location["tabId"] = tab_id
    return {
        "insertTable": {
            "location": location,
            "rows": rows,
            "columns": cols,
        }
    }


def _render_inline_with_styles(
    children,
    base_index: int,
    tab_id: Optional[str],
) -> tuple[str, list[dict]]:
    """Walk inline tokens, returning plain text and style requests.

    Args:
        children - inline tokens from markdown-it
        base_index - the document index where this inline block starts
        tab_id - optional tab ID for ranges

    Returns:
        (plain_text, style_requests). The caller emits insertText with
        plain_text starting at base_index, then appends the style_requests.
    """
    text_parts: list[str] = []
    style_requests: list[dict] = []
    local_pos = 0  # position within this inline block (0-based)
    # Stack entries are tuples. For strong/em: (style_name, start_local_pos).
    # For link: (style_name, start_local_pos, href).
    stack: list[tuple] = []

    for tok in children:
        if tok.type == "text":
            text_parts.append(tok.content)
            local_pos += _utf16_len(tok.content)
        elif tok.type == "softbreak":
            text_parts.append(" ")
            local_pos += 1
        elif tok.type == "hardbreak":
            text_parts.append("\n")
            local_pos += 1
        elif tok.type == "code_inline":
            # self-contained - emit style immediately
            start_local = local_pos
            text_parts.append(tok.content)
            local_pos += _utf16_len(tok.content)
            _append_text_style(
                style_requests,
                base_index + start_local,
                base_index + local_pos,
                {"weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}},
                "weightedFontFamily",
                tab_id,
            )
        elif tok.type in ("strong_open", "em_open"):
            stack.append((tok.type, local_pos))
        elif tok.type in ("strong_close", "em_close"):
            opener_type = tok.type.replace("_close", "_open")
            for idx in range(len(stack) - 1, -1, -1):
                if stack[idx][0] == opener_type:
                    _, start_local = stack.pop(idx)
                    style_key = "bold" if opener_type == "strong_open" else "italic"
                    _append_text_style(
                        style_requests,
                        base_index + start_local,
                        base_index + local_pos,
                        {style_key: True},
                        style_key,
                        tab_id,
                    )
                    break
        elif tok.type == "link_open":
            # tok.attrs may be a dict (newer markdown-it-py) or list of [key, val]
            # pairs (older). Support both.
            href = _token_attr(tok, "href")
            stack.append(("link_open", local_pos, href))
        elif tok.type == "link_close":
            for idx in range(len(stack) - 1, -1, -1):
                if stack[idx][0] == "link_open":
                    _, start_local, href = stack.pop(idx)
                    if href:
                        _append_text_style(
                            style_requests,
                            base_index + start_local,
                            base_index + local_pos,
                            {"link": {"url": href}},
                            "link",
                            tab_id,
                        )
                    break
        elif tok.type == "image":
            src = _token_attr(tok, "src")
            label = tok.content or src or ""
            if label:
                start_local = local_pos
                text_parts.append(label)
                local_pos += _utf16_len(label)
                if src:
                    _append_text_style(
                        style_requests,
                        base_index + start_local,
                        base_index + local_pos,
                        {"link": {"url": src}},
                        "link",
                        tab_id,
                    )
        elif tok.type in ("html_inline", "html_block"):
            text_parts.append(tok.content)
            local_pos += _utf16_len(tok.content)

    return "".join(text_parts), style_requests


def _append_text_style(
    requests: list[dict],
    start: int,
    end: int,
    style: dict,
    fields: str,
    tab_id: Optional[str],
) -> None:
    """Append an updateTextStyle request when Google Docs will accept the range."""
    if end <= start:
        return
    requests.append(_build_text_style(start, end, style, fields, tab_id))


def _token_attr(token, name: str) -> Optional[str]:
    """Return a markdown-it token attr across supported markdown-it-py versions."""
    attrs = token.attrs
    if isinstance(attrs, dict):
        return attrs.get(name)
    return next((attr[1] for attr in attrs or [] if attr[0] == name), None)


def _build_text_style(
    start: int,
    end: int,
    style: dict,
    fields: str,
    tab_id: Optional[str],
) -> dict:
    """Build an updateTextStyle request."""
    rng = {"startIndex": start, "endIndex": end}
    if tab_id:
        rng["tabId"] = tab_id
    return {
        "updateTextStyle": {
            "range": rng,
            "textStyle": style,
            "fields": fields,
        }
    }


def _build_insert_text(index: int, text: str, tab_id: Optional[str]) -> dict:
    """Build an insertText request dict, threading tab_id if provided."""
    location = {"index": index}
    if tab_id:
        location["tabId"] = tab_id
    return {"insertText": {"location": location, "text": text}}


def _build_paragraph_style(
    start: int, end: int, style: dict, tab_id: Optional[str]
) -> dict:
    """Build an updateParagraphStyle request covering every key in style."""
    rng = {"startIndex": start, "endIndex": end}
    if tab_id:
        rng["tabId"] = tab_id
    return {
        "updateParagraphStyle": {
            "range": rng,
            "paragraphStyle": dict(style),
            "fields": ",".join(style),
        }
    }


def _append_spacer(
    requests: list[dict], cursor: list[int], tab_id: Optional[str]
) -> None:
    """Append a blank NORMAL_TEXT spacer paragraph and advance cursor."""
    requests.append(_build_insert_text(cursor[0], "\n", tab_id))
    cursor[0] += 1
    requests.append(
        _build_paragraph_style(cursor[0] - 1, cursor[0], _NORMAL_TEXT, tab_id)
    )


def _append_block_gap(
    requests: list[dict], start: int, end: int, tab_id: Optional[str]
) -> None:
    """Append spaceBelow on the last paragraph of a non-empty block."""
    if end > start:
        requests.append(_build_paragraph_style(end - 1, end, _BLOCK_GAP, tab_id))


def build_markdown_insert_requests(
    markdown_text: str,
    insert_at: int,
    tab_id: Optional[str] = None,
    split_paragraph: bool = False,
    drop_bullet: bool = False,
) -> list[dict]:
    """Build requests inserting markdown at ``insert_at``; [] if it renders empty.

    ``split_paragraph`` opens a new paragraph when ``insert_at`` is mid-paragraph;
    ``drop_bullet`` stops the first rendered paragraph inheriting a bullet.
    """
    content_start = insert_at + 1 if split_paragraph else insert_at
    markdown_requests = markdown_to_docs_requests(
        markdown_text, tab_id=tab_id, start_index=content_start
    )
    if not markdown_requests:
        return []

    requests: list[dict] = []
    if split_paragraph:
        requests.append(create_insert_text_request(insert_at, "\n", tab_id))
    if drop_bullet:
        requests.append(
            create_delete_bullet_list_request(content_start, content_start + 1, tab_id)
        )
    requests.extend(markdown_requests)
    return requests


def build_section_markdown_requests(
    section: dict,
    markdown_text: str,
    append: bool,
    tab_id: Optional[str] = None,
) -> list[dict]:
    """Build requests replacing, or appending to, a heading section with markdown."""
    body_start, section_end = section["body_start"], section["section_end"]
    last_paragraph = section["last_paragraph"]
    at_body_end = section_end == last_paragraph["endIndex"] - 1
    drop_bullet = at_body_end and "bullet" in last_paragraph.get("paragraph", {})
    if append or section_end <= body_start:
        return build_markdown_insert_requests(
            markdown_text,
            section_end,
            tab_id,
            split_paragraph=at_body_end and last_paragraph["startIndex"] < section_end,
            drop_bullet=drop_bullet,
        )
    insert_requests = build_markdown_insert_requests(
        markdown_text, body_start, tab_id, drop_bullet=drop_bullet
    )
    if not insert_requests:
        return []
    return [
        create_delete_range_request(body_start, section_end, tab_id),
        *insert_requests,
    ]
