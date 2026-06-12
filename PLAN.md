# PLAN — Section-Addressable Markdown Editing for Google Docs

Branch: `feat/section-edit-tools`
Status: DRAFT v4 — after round-3 verification.

## Round-3 verification highlights folded in

Two scopes (preservation, style) concluded "ready to ship after polish." API and Deploy found real BUGs needing v4. Changes:

**API:**
- `doc["revisionId"]` → `doc.get("revisionId")` + `UserInputError` if absent (read-only tokens omit the field).
- Heading-text comparison uses `rstrip("\n")` not single-char strip. Headings containing inline objects (footnoteReference, pageBreak, equation, inlineObjectElement) are not reliably matchable — documented as a limitation.
- `body_protected_end_index` no longer panics if last element is not a paragraph (the Docs API contract does NOT guarantee this). Walks backward through `body.content` to find the last paragraph; falls back to last element's `endIndex - 1` if none.
- **Footnote-reference pre-check PRESCRIBED** (not deferred to T7). `find_heading_range` now also returns a `footnote_ref_count` for the candidate range; the section-edit tools raise `UserInputError` with a clear remediation if `> 0`. T7 becomes a positive verification rather than a gating discovery test.
- **Nonce dropped** from `copy_doc_as_snapshot`. Replaced with a `files.list` dedup pre-check: if a file with the proposed name already exists in the target folder, return its ID instead of creating a duplicate. Idempotent for the retry case; clear for the new-snapshot case.

**Preservation (matrix tightening; no implementation changes):**
- Mid-batch race claim softened: `requiredRevisionId` prevents the read-then-write race but does NOT promise transaction isolation against edits that land DURING batch application.
- T7 cell split into T7-a (segment retained) vs T7-b (400). Since v4 prescribes the pre-check, this becomes informational rather than gating.
- `replace_doc_section_by_heading` "Comments inside" → "deleted=false in Drive API" promoted from Inferred to Verified (verified by absence of any orphan-filter parameter in `comments.list`).
- Snapshot failure modes (quota, folder-write permission, restricted-copy) documented in `copy_doc_as_snapshot` docstring. The skill amendment will gate destructive edits on snapshot success.
- **Evidence column added** to preservation matrix: V cells cite the API doc; I cells cite the integration test.

**Style:**
- `copy_doc_as_snapshot`: bare `service` (no `: Any`) AND explicit `is_read_only=False` to match `drive_tools.py` file-local convention (`copy_drive_file:2385`). The 3 docs tools keep `service: Any` and omit `is_read_only=False` to match `docs_tools.py` write-tool convention.
- Upfront `documents.get` wrapped in `asyncio.wait_for(..., timeout=30)` per `get_doc_as_markdown:2421` precedent — defensive against large-doc fetches.

**Deploy (critical):**
- **Container USER mismatch resolved.** Upstream Dockerfile uses `USER app` (HOME=/home/app), credentials would resolve to `/home/app/.google_workspace_mcp/credentials`. Currently-deployed image runs as root and the compose volume is mounted at `/root/.google_workspace_mcp/credentials/`. **Decision: build from a fork-local Dockerfile patch that omits the `USER app` line** (preserves the current root-based deployment without altering compose volume mounts). The patch is one commit on `feat/section-edit-tools` and clearly NOT upstream-able — split into a separate `deploy-only` branch IF the v4 deploy approach is greenlit by round-4 verification.
- Tag base: explicitly cite vendor-fork-deploy "single PR, one-shot fix" carve-out (skill line 86). Feature-branch SHA is intentional; no `deployed` branch created.
- Nango URL: `https://nango.flicker-duckbill.ts.net` (no port, Tailscale Service since 2026-05-20 per Services-Inventory.md:51).
- `PullImage: false` aligned to canonical (per portainer-stack-operations skill); harmless since the SHA-suffixed tag is new.
- Smoke-test script path: `~/admin-technical/setup/synology/google-workspace-mcp-shared/scripts/smoke_test_tool_list.py` (new `-shared/` dir; the existing `-john/`, `-frisbee/`, `-max/` are per-stack and not the right home for cross-stack tooling). Script pins `mcp` SDK version: `uvx --with mcp==1.12.4 python ...`.
- Smoke-test throwaway doc IDs stored in 1Password: `Google Workspace MCP - Smoke Test Doc ID (john)` and `(frisbee)` in JRVIS Infra vault.
- **Rollback flow fixed.** Compose edits split into TWO commits (`deploy(google-workspace-mcp-john): ...` and `deploy(google-workspace-mcp-frisbee): ...`) so `git revert <frisbee-commit>` cleanly reverts just frisbee without touching john's compose. The v3 syntax `git revert <commit> -- path` is invalid.
- John's stack ID looked up + embedded (replaces `TODO` placeholder).
- Nango scope-broadening procedure: documented as "delete + recreate connection" if Nango UI rejects scope changes on existing connection.

## Context

The Docs API addresses edits by `(startIndex, endIndex)` character offsets in a structural-element tree. Agents struggle: computing the right indices requires reading the doc, walking the element tree, and threading paragraph/text-run/inline-style boundaries. Typical agent failures:

- Falling back to `import_to_google_doc` against an existing doc (which creates a NEW file with a NEW ID, breaking every external link), OR
- Issuing dozens of `batch_update_doc` calls with hand-computed indices that drift after the first insertion.

This MCP already has the two primitives needed:

1. **`get_doc_as_markdown`** — pulls current state with comment context (`gdocs/docs_tools.py:2364`)
2. **`markdown_to_docs_requests(markdown_text, tab_id, start_index)`** — converts CommonMark to a list of Docs API batchUpdate request dicts ready to apply at any start index (`gdocs/docs_markdown_writer.py:23`)

What's missing is **section-addressable editing**: tools that locate a target range by **heading text** (or doc-wide), delete the range, and insert markdown-converted requests at the deleted slot — all in one atomic `batchUpdate` guarded by `requiredRevisionId`. Plus a real safety net: **snapshot-by-copy** since Drive revisions can't be named or pinned via API for native Docs.

## Scope — 4 new MCP tools

### 1. `replace_doc_section_by_heading` (destructiveHint=True)

```python
@server.tool(
    title="Replace Doc Section by Heading",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True,
        idempotentHint=False, openWorldHint=True,
    ),
)
@handle_http_errors("replace_doc_section_by_heading", service_type="docs")
@require_google_service("docs", "docs_write")
async def replace_doc_section_by_heading(
    service: Any,
    user_google_email: str,
    document_id: str,
    heading_text: str,
    new_markdown: str,
    heading_level: Optional[int] = None,
    match: Literal["first", "exact"] = "first",
    tab_id: Optional[str] = None,
) -> str:
    """Replace a heading-delimited section's contents with new markdown.

    Locates the heading paragraph whose visible text (after rstrip("\\n"))
    equals heading_text (case-sensitive, full string). If heading_level is
    given, also requires that level. The "section" runs from the heading
    paragraph's startIndex through (exclusive) the next paragraph whose
    namedStyleType is a heading at equal-or-shallower level (or TITLE) — or
    body_protected_end_index() if no such heading follows.

    Limitations:
      - Body-only matching. Headings inside table cells, footnotes, headers,
        or footers are NOT addressable. Use batch_update_doc for those.
      - If the heading appears ONLY in a header/footer/footnote/table-cell,
        this tool raises "no match" even though the heading literally exists
        in the document.
      - Headings whose paragraph contains inline objects (footnoteReference,
        pageBreak, equation, inlineObjectElement) are not reliably matchable
        by text — _extract_paragraph_text concatenates only textRun.content.
      - If the candidate section range contains any footnote references, this
        tool raises UserInputError with a remediation message (the API's
        behavior on cross-footnoteRef delete is undocumented; pre-checking
        is safer than discovering at runtime).

    Atomicity: one batchUpdate with WriteControl.requiredRevisionId set to
    the revisionId returned by the documents.get call that computed indices.
    If a concurrent edit lands between the read and the write, the API
    returns 400 with FAILED_PRECONDITION-style body and no changes are
    applied. The tool does NOT auto-retry (concurrent edits often mean
    indices shifted semantically, not just numerically).

    Note: requiredRevisionId prevents the read-then-write race window but
    does NOT promise cross-batch transaction isolation against edits that
    land DURING batch application. In practice the application window is
    short (server-side execution is fast), and per-request validation still
    holds — but if a downstream tool requires strict isolation, use
    copy_doc_as_snapshot first and verify the snapshot's content after.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        heading_text: Visible text of the heading paragraph to match
        new_markdown: Replacement markdown. CommonMark only — GFM tables,
            strikethrough, task lists, autolinks are not supported.
        heading_level: Optional H1-H6 level requirement (1-6)
        match: "first" takes first occurrence; "exact" errors on multiple
        tab_id: Tab ID for multi-tab docs. REQUIRED if doc has >1 tab;
            UserInputError lists available tab IDs otherwise.

    Returns:
        Single-line confirmation: characters deleted, requests applied, new
        section range, and the doc's edit link. Matches update_paragraph_style
        return shape (docs_tools.py:2341).
    """
```

### 2. `append_doc_after_heading` (destructiveHint=False)

```python
@server.tool(
    title="Append Doc after Heading",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=False, openWorldHint=True,
    ),
)
@handle_http_errors("append_doc_after_heading", service_type="docs")
@require_google_service("docs", "docs_write")
async def append_doc_after_heading(
    service: Any,
    user_google_email: str,
    document_id: str,
    heading_text: str,
    new_markdown: str,
    heading_level: Optional[int] = None,
    match: Literal["first", "exact"] = "first",
    tab_id: Optional[str] = None,
) -> str:
    """Insert markdown at the end of a heading-delimited section.

    Section-end computation same as replace_doc_section_by_heading. No
    deletion — pure insertion at section_end. Nothing is orphaned because
    nothing is deleted. Same multi-tab requirements, body-only restrictions,
    and WriteControl.requiredRevisionId guard. No footnote-ref pre-check
    needed (we don't delete).

    Note: if new_markdown begins with a heading re-stating the section
    heading, the result is a duplicate heading — caller's responsibility.
    """
```

### 3. `replace_doc_fully_from_markdown` (destructiveHint=True)

```python
@server.tool(
    title="Replace Doc Fully from Markdown",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True,
        idempotentHint=False, openWorldHint=True,
    ),
)
@handle_http_errors("replace_doc_fully_from_markdown", service_type="docs")
@require_google_service("docs", "docs_write")
async def replace_doc_fully_from_markdown(
    service: Any,
    user_google_email: str,
    document_id: str,
    new_markdown: str,
    tab_id: Optional[str] = None,
) -> str:
    """Replace the entire body of a Doc with new markdown.

    Computes body_protected_end via body_protected_end_index() — explicitly
    excludes the trailing newline (whose deletion is rejected by the API).

    Single batchUpdate with WriteControl.requiredRevisionId:
      1. DeleteContentRangeRequest(1, body_protected_end) [skipped if body
         is the empty default — body_protected_end <= 1]
      2. The output of markdown_to_docs_requests(new_markdown, tab_id, 1)

    If the body contains footnote references, this tool also raises
    UserInputError (same pre-check as replace_doc_section_by_heading).

    Document ID survives — distinct from import_to_google_doc, which
    creates a NEW file with a NEW ID.

    Preservation:
      - Body contents wiped: comments orphaned in UI (Verified that they
        remain deleted=false in Drive API); suggestions silently dropped;
        body named ranges lost.
      - Headers, footers, footnote SEGMENTS survive — cross-segment delete
        is not expressible (the API's Range has a single segmentId).
      - NamedRanges with ranges[] entries in both body and non-body
        segments lose only their body entries; non-body entries persist as
        discrete Range objects in the NamedRange.
      - Doc metadata, sharing, revision history all preserved.
    """
```

### 4. `copy_doc_as_snapshot` (destructiveHint=False)

```python
@server.tool(
    title="Copy Doc as Snapshot",
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False,
        idempotentHint=False, openWorldHint=True,
    ),
)
@handle_http_errors("copy_doc_as_snapshot", is_read_only=False, service_type="drive")
@require_google_service("drive", "drive_file")
async def copy_doc_as_snapshot(
    service,
    user_google_email: str,
    document_id: str,
    name: Optional[str] = None,
    folder_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """Create a snapshot copy of a Google Doc as a sibling file.

    Why this tool exists: Drive API revisions for native Docs cannot be
    pinned (keepForever is binary-file-only) or named (no `name` field on
    Revision resource for native files). Instead, this tool creates an
    independent copy via Drive files.copy — a full-fidelity snapshot the
    owner can compare, revert from, or trash after the edit lands.

    Idempotency: before creating the snapshot, calls files.list with
    q="name = '<computed name>' and '<folder_id>' in parents and trashed
    = false" — if a matching file already exists, returns its ID without
    creating a duplicate. Agents that retry the same call (with the same
    timestamp argument) get the same snapshot back. This replaces v3's
    nonce design, which couldn't actually deduplicate (Drive permits
    duplicate names; nonce only varied the names, didn't prevent dup files).

    Comments and suggestions are NOT carried to the snapshot. Drive UI's
    "Make a copy" offers checkboxes to copy them; files.copy API does not
    (Verified: no copyComments / copyRequestingUser parameter exists in
    the API reference). If the human expects thread fidelity, they should
    use the Drive UI instead.

    Failure modes the caller MUST handle:
      - 403 storageQuotaExceeded — user is over Drive quota
      - 403 insufficientFilePermissions — folder-write or restricted-copy
        (source's `viewersCanCopyContent` set to false by owner)
      - 404 — source not accessible via the granted scope
    If this tool returns an error, the safety net was NOT created — do
    NOT proceed with the destructive edit assuming rollback is available.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        name: Snapshot file name. Defaults to
            "{original_title}.snapshot.{compact-ISO-UTC}" where the timestamp
            is the caller-supplied UTC ISO 8601 (compact form
            YYYYMMDDTHHMMSSZ — no colons for cross-platform safety, not a
            Drive API restriction). Agents supply the timestamp because
            this MCP runs in a stateless container.
        folder_id: Optional parent folder ID. Defaults to all of the
            source's parents (rare in modern Drive due to single-parent
            enforcement; safe).
        timestamp: Compact ISO 8601 UTC timestamp. Required if name is
            None; ignored if name is given.

    Returns:
        Multi-line confirmation per copy_drive_file convention
        (drive_tools.py:2441-2453):
          Successfully created snapshot of '<original_title>'
          Original file ID: <id>
          Snapshot file ID: <id>
          Snapshot name: <name>
          Location: <parent_folder_id>
          View snapshot: <webViewLink>
          View source: <source_webViewLink>
          For human-visible rollback, paste this comment on the source:
            "Snapshot before agent edit: <snapshot_webViewLink>"

    Notes:
      - supportsAllDrives=True is passed so this works on docs in shared
        drives (matches copy_drive_file at gdrive/drive_tools.py:2435).
      - drive_file is the declared minimum scope; the deployed token must
        hold full drive scope to copy docs not created by this app
        (drive.file alone limits to files the app created or that the user
        opened via Picker). The deployed Nango-fed token holds full drive.
      - Snapshot starts a fresh revision history (Inferred — verify T1).
    """
```

**Module placement:** `gdrive/drive_tools.py`.

## Implementation

### Shared helpers (in `gdocs/docs_structure.py`)

```python
def find_heading_range(
    doc: dict,
    heading_text: str,
    heading_level: Optional[int],
    match: Literal["first", "exact"],
    tab_id: Optional[str],
) -> tuple[int, int, int]:
    """Locate a heading paragraph and the end of its section.

    Returns (section_start, section_end, footnote_ref_count_in_range).
    The third value lets the section-edit tools fail fast with a clear
    message if footnote references lie inside the section, since the
    API's behavior on cross-footnoteRef delete is undocumented.

    Walks body.content (single-tab or top-level body) or the matching
    tab's documentTab.body.content (multi-tab). Does NOT recurse into
    table cells, footnotes, headers, or footers.

    Match criteria:
      - extract text from paragraph.elements[*].textRun.content
      - rstrip("\\n") then full-string case-sensitive equality with heading_text
      - if heading_level given, paragraphStyle.namedStyleType must equal
        f"HEADING_{heading_level}"
      - namedStyleType defaults to "NORMAL_TEXT" when key absent

    Section end:
      - startIndex of the next paragraph with namedStyleType in
        {HEADING_1..HEADING_<= matched_level, TITLE} — SUBTITLE NOT a terminator
      - clamped to body_protected_end_index(doc, tab_id) if no successor

    Multi-tab:
      - if doc has >1 tab and tab_id is None: raises UserInputError with
        message listing available tab IDs from doc.tabs[*].tabProperties.tabId
      - if tab_id given: walks the matching tab body; UserInputError if not found

    Raises UserInputError on no match, on (match="exact" AND multiple matches),
    or if the matched heading paragraph itself contains inline objects (which
    would make the matched text unreliable).
    """
```

```python
def body_protected_end_index(doc: dict, tab_id: Optional[str] = None) -> int:
    """Return the largest index passable as DeleteContentRangeRequest.endIndex
    without triggering "Deleting the last newline character of a Body."

    The Docs API contract does NOT guarantee the last body element is a
    paragraph (Body StructuralElement = paragraph | sectionBreak | table |
    tableOfContents). Walks backward through body.content to find the last
    paragraph and returns its endIndex - 1. If no paragraph exists at all
    (theoretically possible per spec), falls back to last element's
    endIndex - 1 with a debug log entry. Behavior is Inferred-not-Verified
    for the no-paragraph case.
    """
```

```python
def count_footnote_refs_in_range(
    doc: dict, tab_id: Optional[str], start: int, end: int,
) -> int:
    """Count footnoteReference elements in body.content (or matching tab)
    whose startIndex falls within [start, end). Used to pre-check the
    target section before issuing DeleteContentRangeRequest.
    """
```

### Atomic batchUpdate pattern (used by all 3 section tools)

```python
doc = await asyncio.wait_for(
    asyncio.to_thread(
        service.documents()
        .get(documentId=document_id, includeTabsContent=True)
        .execute
    ),
    timeout=30,
)
revision_id = doc.get("revisionId")
if not revision_id:
    raise UserInputError(
        "WriteControl unavailable — the token lacks edit access on this document."
    )
section_start, section_end, footnote_count = find_heading_range(doc, ...)
if footnote_count > 0:
    raise UserInputError(
        f"Section contains {footnote_count} footnote reference(s); use "
        "batch_update_doc for surgical edits, or copy_doc_as_snapshot first."
    )
requests = [
    {"deleteContentRange": {"range": {"startIndex": section_start, "endIndex": section_end}}},
    *markdown_to_docs_requests(new_markdown, tab_id=tab_id, start_index=section_start),
]
await asyncio.to_thread(
    service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": requests,
            "writeControl": {"requiredRevisionId": revision_id},
        },
    ).execute
)
# Code comment at this site: the delete-then-insert order is safe because the
# inserts emitted by markdown_to_docs_requests use cursor=section_start, which
# is valid in the post-delete document state. Reordering breaks index coherence.
```

### Atomic-commit plan

Each commit is a green-tests + green-lint checkpoint. Order:

1. `chore: add PLAN.md for section-edit tools` (committed: `177c7f9`)
2. `chore: revise PLAN.md after round-1 verification` (committed: `43581fe`)
3. `chore: revise PLAN.md after round-2 verification` (committed: `9c859a8`)
4. `chore: revise PLAN.md after round-3 verification` (this commit)
5. `feat(gdocs): add find_heading_range, body_protected_end_index, count_footnote_refs_in_range helpers`
6. `chore: register placeholder entries in tool_tiers.yaml for upcoming section tools`
7. `feat(gdocs): add replace_doc_section_by_heading tool`
8. `feat(gdocs): add append_doc_after_heading tool`
9. `feat(gdocs): add replace_doc_fully_from_markdown tool`
10. `feat(gdrive): add copy_doc_as_snapshot tool`
11. `test(gdocs): unit tests for find_heading_range + section tools (mocked)`
12. `test(gdrive): unit test for copy_doc_as_snapshot (mocked)`
13. `docs(README): add 4 new tools to Docs/Drive tables; add preservation matrix appendix`

Each commit:
- passes `uv run pytest tests/ -m "not integration"`
- passes `uv run ruff check .` and `uv run ruff format --check .` (project default: 88 col)
- includes `Claude-Session-Id` + `Resume:` trailers per CLAUDE.md
- body wrapped at 72 cols

## Test plan

### Mocked unit tests (UPSTREAM PR — `tests/gdocs/test_section_edit.py` and `tests/gdrive/test_copy_doc_as_snapshot.py`)

Uses `unittest.mock` + per-file `_unwrap` helper (per `test_advanced_doc_formatting.py:21-26` precedent — no shared `conftest.py` for this exists; extracting it is out of scope).

Tests:
- `find_heading_range`: no match, single match, multiple match w/ `match="first"`, multiple w/ `match="exact"` (errors), nested H1/H2/H3, TITLE as level-0 terminator, SUBTITLE NOT a terminator, heading at end of body (clamped), heading inside a tab, multi-tab w/ `tab_id=None` (errors), `namedStyleType` absent (defaults NORMAL_TEXT), heading text with and without trailing newline, heading containing inline objects (raises clear error), section containing footnote refs (returns count > 0).
- `body_protected_end_index`: empty body, single heading, multiple top-level blocks, body ending in a non-paragraph element (walks backward to find last paragraph), body with zero paragraphs (fallback path).
- `count_footnote_refs_in_range`: zero, one, multiple, refs straddling range boundary.
- `replace_doc_section_by_heading`: asserts exactly one `batchUpdate`; asserts `writeControl.requiredRevisionId` is set; asserts request order (delete then inserts); asserts inserts start at `section_start`; asserts footnote-ref pre-check raises before any API call.
- `append_doc_after_heading`: asserts one `batchUpdate` with inserts only at `section_end`; `requiredRevisionId` set.
- `replace_doc_fully_from_markdown`: asserts delete `(1, body_protected_end)` precedes inserts at index 1; skips delete on near-empty doc; `requiredRevisionId` set.
- `copy_doc_as_snapshot`: asserts `files.list` dedup check; if found, returns existing snapshot ID; if not, `files.copy` called with the right `name`, `parents`, `supportsAllDrives=True`.

### Integration tests (LOCAL ONLY — `tests/integration/test_section_edit_live.py`, `pytest.mark.integration`)

Throwaway doc in johntrandall@gmail.com pre-populated with headings A/B/C, comments + suggestions in known positions, a footnote in C, a named range straddling B's boundary.

| Test | Action | Verify |
|---|---|---|
| T1 | `copy_doc_as_snapshot(doc)` then again | Second call returns the first snapshot's ID (dedup); snapshot starts fresh revision history |
| T2 | `replace_doc_section_by_heading(doc, "Section B", ...)` | A's comment still anchored; C's suggestion still live; B contents = "Replaced."; record Drive API `comment.deleted` for orphaned B comment; record suggester notification |
| T3 | `append_doc_after_heading(doc, "Section A", ...)` | A's comment still anchored; new para between A2 and `## Section B` |
| T4 | `replace_doc_fully_from_markdown(doc, ...)` | Doc ID unchanged; record orphaned-comment Drive API state; header/footer survive |
| T5 | Section delete that spans a named range | Record shrink-vs-split observation |
| T6 | Section delete includes a comment straddling boundary | Record Drive API state vs UI state |
| T7-a/b | Section delete includes a footnote ref | The tool's pre-check raises UserInputError WITHOUT issuing batchUpdate. T7 is now a positive verification that the pre-check fires, not a gating discovery of API behavior. |
| T8 | Multi-tab doc, no `tab_id` arg | UserInputError lists tab IDs |
| T9 | Concurrent-edit race | 400 returned; no partial application |

### Preservation matrix (canonical — goes in README appendix)

Legend: ✅ = kept • ⚠ = docstring-warning required • ❌ = destroyed • n/a = not applicable.
Confidence: V = Verified against Google docs (cited in Evidence) • I = Inferred (test cited in Evidence).

| Tool | Comments outside | Comments inside | Comments straddle | Suggestions outside | Suggestions inside | Body named ranges outside | Hdr/ftr/ftnote NRs | Footnote refs in deleted body | Doc ID | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| `replace_doc_section_by_heading` | ✅ I (T2) | ⚠ V (UI orphan; comments.list `deleted=false` verified by absence of orphan filter in API) | ⚠ I (T6) | ✅ I (T2) | ⚠ I (silent drop; notify behavior unverified — T2) | ⚠ I (shrink-or-split — T5) | ✅ V (Range.segmentId schema) | ⚠ pre-check raises before any delete (T7-a/b informational) | ✅ V (batchUpdate operates on fileId path param) | docs cited inline |
| `append_doc_after_heading` | ✅ V | n/a | n/a | ✅ V | n/a | ✅ V | ✅ V | n/a | ✅ V | — |
| `replace_doc_fully_from_markdown` | n/a | ⚠ V | n/a | n/a | ⚠ I (T4) | ❌ V (body wiped) | ✅ V (NamedRanges' non-body Range entries persist) | ⚠ pre-check raises (T7) | ✅ V | docs cited inline |
| `copy_doc_as_snapshot` | ✅ V (source unchanged) | ✅ V | n/a | ✅ V | n/a | ✅ V | ✅ V | n/a | ✅ V (snapshot has new ID; source ID unchanged) | files.copy API ref (no copyComments param) |

## Skill integration

The `google-docs-versioning` skill needs amendment:

- Replace `create_version` references (Apps Script in this MCP) with `copy_doc_as_snapshot`.
- Naming convention: `<original_title>.snapshot.<compact-iso-utc>` (no nonce — dedup handled by tool).
- Document Drive-UI-vs-API thread-fidelity caveat.
- Document the snapshot failure modes (quota, permission, restricted-copy) and require the agent to verify snapshot success BEFORE issuing destructive edits.
- Cross-link the 3 section-edit tools as the safe-by-default editing primitives.
- Cleanup: snapshot files don't auto-expire; per-quarter cleanup pass on `*.snapshot.*`.

## Backwards compatibility

- No existing tools renamed, removed, or signature-changed.
- 4 new tools, 3 new helpers; no shared mutable state.
- `markdown_to_docs_requests` consumed unchanged.
- `tool_tiers.yaml`: 4 new entries under `extended` tier (matches `find_and_replace_doc`/`insert_doc_elements`/`update_paragraph_style` precedent).

## Upstream PR readiness

- `uv run pytest tests/ -m "not integration"` passes.
- `uv run ruff check .` and `uv run ruff format --check .` pass.
- README updated: 4 new rows in Docs + Drive tables (matching `<sub>` formatting + Tier badge at lines 768-786 / 848-871). Preservation matrix added as appendix.
- Docstrings follow Google style with `Args:` / `Returns:` blocks.
- Conventional-commit subjects.
- `Allow edits from maintainers` checked (enforced by `.github/workflows/check-maintainer-edits.yml` — PR cannot merge without it on fork PRs).
- PR description: motivation, design summary, preservation matrix, local integration-test outputs.

## Deploy plan

### Image build (vendor-fork-deploy, single-PR carve-out)

Per vendor-fork-deploy skill's "When NOT to use this pattern" carve-out (skill line 86): single PR, one-shot feature. No `deployed` branch; tag SHA comes from the feature branch.

Upstream Dockerfile (`Dockerfile:27`) uses `USER app` (HOME=/home/app). Currently-deployed image runs as root with volume mounts at `/root/.google_workspace_mcp/credentials/`. To preserve compose contract without altering volume paths, build with a one-line fork-local patch dropping `USER app`. This patch is NOT upstream-able and lives on a sibling `deploy/section-edit-tools-root-user` branch off `feat/section-edit-tools` (merged at deploy time only).

```bash
cd ~/dev/google_workspace_mcp
git checkout -b deploy/section-edit-tools-root-user feat/section-edit-tools
# remove the USER app line from Dockerfile
git commit -am "deploy-only: drop USER app for root-based credential volume compat"
SHA=$(git rev-parse --short HEAD)
TAG="v1.21.2-jtr-${SHA}"

docker buildx build --platform linux/amd64 \
  -t localhost/vendor/google-workspace-mcp:${TAG} \
  --load .   # --load (not --push) because we route via skopeo for OCI manifest

skopeo copy --dest-tls-verify=false --format oci \
  docker-daemon:localhost/vendor/google-workspace-mcp:${TAG} \
  docker://umbridge:5050/vendor/google-workspace-mcp:${TAG}
```

### Compose edits (two stacks, SEPARATE commits)

Edit each, separate commit per stack (enables clean per-stack rollback via `git revert <single-commit>`):

```bash
cd ~/dev/portainer-stacks
# edit google-workspace-mcp/docker-compose.yml: image -> :${TAG}
git add google-workspace-mcp/docker-compose.yml
git commit -m "deploy(google-workspace-mcp-john): v1.21.2-jtr-<sha> — section-edit tools"

# edit google-workspace-mcp-frisbee/docker-compose.yml: image -> :${TAG}
git add google-workspace-mcp-frisbee/docker-compose.yml
git commit -m "deploy(google-workspace-mcp-frisbee): v1.21.2-jtr-<sha> — section-edit tools"

git push forgejo-umbridge main
```

(Push identity: `John Randall <john@johnrandall.com>` per `git log` precedent in `portainer-stacks`.)

### Portainer git-redeploy

Stack IDs:
- `google-workspace-mcp` (john, port 8900): **ID to look up via `GET /api/stacks?filters={"Name":"google-workspace-mcp"}` and paste here before deploy**
- `google-workspace-mcp-frisbee` (port 8931): ID 260 (per inventory)

For each stack:
1. `GET /api/stacks/{id}` → capture `Env` array literally.
2. `PUT /api/stacks/{id}/git/redeploy` with body:
   ```json
   {
     "Env": <preserved-env-array>,
     "RepositoryAuthentication": true,
     "RepositoryUsername": "john",
     "RepositoryPassword": "<op read 'op://JRVIS Infra/Forgejo - Portainer GitOps Token/credential'>",
     "RepositoryReferenceName": "refs/heads/main",
     "Prune": true,
     "PullImage": false
   }
   ```
(`Prune: true` removes orphan compose services; does NOT delete the old image tag — rollback target stays pullable. `PullImage: false` matches canonical; since SHA-suffixed tag is new, Docker pulls anyway on a cache miss.)

### Token / scope check (pre-deploy)

The deployed token must include `https://www.googleapis.com/auth/drive` (full) and `.../auth/documents`. Verify per stack:

```bash
ssh infra-agent@umbridge \
  "docker exec google-workspace-mcp-john \
   cat /root/.google_workspace_mcp/credentials/johntrandall@gmail.com.json" \
  | jq -r '.scopes[]'
```

(Container-internal path is `/root/...` because the deploy-only branch drops `USER app`; if we ever switch to the upstream `USER app` model, this becomes `/home/app/...` and the compose volume target needs to change too. Pre-deploy sanity check: `docker inspect <image> | jq -r '.[0].Config.User // "root"'` must match the volume path's home prefix.)

If `.../auth/drive` is absent: re-auth via Nango (NOT local `uvx workspace-mcp` bootstrap):

1. Update OAuth client / consent screen in GCP to include full `drive` scope.
2. In Nango UI at `https://nango.flicker-duckbill.ts.net`, re-authorize `google-workspace-john` and `google-workspace-frisbee` connections. If Nango rejects scope changes on existing connections, **delete and recreate** the connection (the `connection_id` and `provider_config_key` must stay identical; if you change them, the token feeder env needs updating too).
3. `docker restart google-workspace-token-feeder` (and frisbee equivalent) to force a fresh fetch.
4. Re-verify via the `docker exec cat ... | jq` command above.

### Smoke test (post-deploy)

Lives at `~/admin-technical/setup/synology/google-workspace-mcp-shared/scripts/smoke_test_tool_list.py` (new `-shared/` dir for cross-stack tooling). Run via:

```bash
DOC_ID=$(op read "op://JRVIS Infra/Google Workspace MCP - Smoke Test Doc ID (john)/credential")
uvx --with mcp==1.12.4 python smoke_test_tool_list.py http://umbridge:8900/mcp "$DOC_ID"
# then for frisbee:
DOC_ID=$(op read "op://JRVIS Infra/Google Workspace MCP - Smoke Test Doc ID (frisbee)/credential")
uvx --with mcp==1.12.4 python smoke_test_tool_list.py http://umbridge:8931/mcp "$DOC_ID"
```

Script:
1. Connects via Streamable HTTP, initializes session, calls `tools/list`.
2. Asserts the 4 expected tool names present.
3. Calls `copy_doc_as_snapshot` against the throwaway doc — verifies runtime health (not just registration). Then deletes the snapshot.
4. Prints PASS/FAIL summary.

### Rollback flow

- John smoke fails: `cd ~/dev/portainer-stacks && git revert <john-deploy-commit-sha> && git push forgejo-umbridge main`, then re-trigger Portainer redeploy of john. Frisbee untouched.
- Frisbee smoke fails after john passes: `git revert <frisbee-deploy-commit-sha>`, push, redeploy frisbee. John stays on new tag.
- Image-level bug (affects BOTH): revert BOTH compose commits, push, redeploy both. The old image tag remains in Zot for the rollback to pull (`Prune` doesn't remove images).
- If smoke test fails on first call: read `docker logs google-workspace-mcp-john --tail 200` for import errors.

### Order of operations

1. Build + push image (Mac → Umbridge Zot).
2. Verify scopes per stack; Nango re-auth if needed.
3. Push compose edits (TWO commits) to `infra/portainer-stacks`.
4. Redeploy john → smoke test john.
5. Redeploy frisbee → smoke test frisbee.

## Risks + open questions (FOR ROUND-4 VERIFIERS)

Resolved in v4:
1. ✅ Footnote-ref pre-check prescribed.
2. ✅ Nonce design dropped; replaced with `files.list` dedup.
3. ✅ `body_protected_end_index` walks backward.
4. ✅ `doc.get("revisionId")` with clear error.
5. ✅ Container USER/credential-path mismatch handled via deploy-only branch.
6. ✅ Compose split into two commits for clean per-stack rollback.
7. ✅ Smoke-test script home directory chosen (`-shared/`).
8. ✅ Nango URL updated.

Small remaining items for round-4 challenge:
1. The fork-local Dockerfile patch (drop `USER app`) is a maintenance burden. Worth pursuing the alternative: update both compose files to mount the credential volume at `/home/app/.google_workspace_mcp/credentials/` AND `chown -R app:app` the existing volume contents. One-time per-stack chown; aligns with upstream's security improvement. Defer to round 4 for the tradeoff judgment.
2. T1 must verify "snapshot starts fresh revision history" — if it inherits, the matrix cell is wrong.
3. T2 must record whether suggesters get any notification when their suggestion is silently dropped.
