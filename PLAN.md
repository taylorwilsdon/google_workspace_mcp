# PLAN — Section-Addressable Markdown Editing for Google Docs

Branch: `feat/section-edit-tools`
Status: DRAFT v3 — after round-2 verification.

## Round-2 verification highlights folded in

Four adversarial verifiers (API, preservation, style, deploy) ran round 2 against v2. No fatal design flaws remain; round-2 found precision issues and one new-in-v2 import-time TypeError. v3 changes:

- **Decorator first-param renamed `service`.** `require_google_service` raises `TypeError` at decoration time if the first parameter isn't literally named `service` (`auth/service_decorator.py:709-713`). v2 used `docs_service` / `drive_service`.
- **`WriteControl.requiredRevisionId` added** to all three section-edit tools. Computed from the `documents.get` that resolves indices, passed in `batchUpdate` to prevent corruption from a concurrent edit shifting indices between read and write.
- **Title casing fixed:** prepositions lowercase per repo convention ("Get Doc as Markdown"). Tool 2 title becomes "Append Doc after Heading" (also adds the "Doc" infix); tool 3 becomes "Replace Doc Fully from Markdown".
- **Linter gate corrected:** project uses `ruff format`, not `black`. Line length is ruff default (88), not 120.
- **`copy_doc_as_snapshot`**: adds `supportsAllDrives=True` to the `files.copy` call (matches existing `copy_drive_file` at `gdrive/drive_tools.py:2387`); switches return to multi-line per `copy_drive_file:2441-2453`; docstring clarifies that the API path does NOT copy comments/suggestions even though Drive UI's "Make a copy" does.
- **Heading-in-table-cell behavior specified:** body-only matching. Headings inside table cells, footnotes, headers, footers are NOT addressable via these tools; use `batch_update_doc` for those.
- **TITLE-only as level-0 terminator:** simpler than v2's TITLE-or-SUBTITLE rule.
- **Heading-text comparison** strips trailing `\n` from the extracted text run before equality check.
- **`body_protected_end_index`** asserts the last element is a paragraph; raises a clear error otherwise.
- **`files.copy` `name`** uses compact ISO 8601 (`20260612T191400Z`) — no colons (cross-platform safety, not Drive folklore); adds a 6-digit nonce to defeat retry collisions.
- **Matrix cells now mark Verified vs Inferred** per CLAUDE.md claim-confidence rule.
- **Deploy section rewritten:**
  - Image tag base: `v1.21.2-jtr-<sha>` (upstream PyPI version per vendor-fork-deploy skill convention; pyproject.toml:7).
  - Build on Mac via OrbStack: `docker buildx build --platform linux/amd64`.
  - Push via `skopeo copy --format oci` to `docker://umbridge:5050/...` (Zot rejects Docker v2 manifests; per Compute-Inventory.md:426-429).
  - Token-volume host path on Synology: `/volume1/@docker/volumes/...` (ADR-097); preferred verification path is `docker exec` into the container.
  - Portainer git-redeploy payload includes `RepositoryAuthentication: true`, `RepositoryUsername`, `RepositoryPassword`, `RepositoryReferenceName: refs/heads/main`, `Prune: true`, `PullImage: true`, and the literal `Env` array (per portainer-stack-operations skill).
  - Nango re-auth path described explicitly (NOT local `uvx workspace-mcp` bootstrap).
  - Stack IDs to be looked up before deploy and pasted in.
  - Two-stack rollback flow added.
  - Smoke-test script lives in `~/admin-technical/setup/synology/google-workspace-mcp/scripts/` (NOT in the upstream fork tree).
- **`is_read_only=False` dropped** from `@handle_http_errors` kwargs (matches the `docs_tools.py` write-tool convention; redundant since default is False).
- **Atomic-commit ordering**: `tool_tiers.yaml` registration moved earlier so intermediate commits don't break `--tools docs|drive` exposure for other operators.
- **`tool_tiers.yaml` note:** registration is upstream-PR-correct but NOT deploy-blocking — the deployed compose has no `TOOL_TIER` env, so the default (no tier filter, all tools) exposes the new tools anyway.

## Context

The Docs API addresses edits by `(startIndex, endIndex)` character offsets in a structural-element tree. Agents struggle: computing the right indices requires reading the doc, walking the element tree, and threading paragraph/text-run/inline-style boundaries. Typical agent failures:

- Falling back to `import_to_google_doc` against an existing doc (which creates a NEW file with a NEW ID, breaking every external link), OR
- Issuing dozens of `batch_update_doc` calls with hand-computed indices that drift after the first insertion (each `InsertTextRequest` shifts every subsequent index).

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
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
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

    Locates the heading paragraph whose visible text (trailing newline stripped)
    equals heading_text (case-sensitive, full string). If heading_level is given,
    also requires that level. The "section" runs from the heading paragraph's
    startIndex through (exclusive) the next paragraph whose namedStyleType is a
    heading at equal-or-shallower level (or TITLE) — or the body's protected
    trailing-newline boundary if no such heading follows.

    Only HEADING_1 through HEADING_6 are matchable. Headings inside table cells,
    footnotes, headers, or footers are NOT addressable via this tool — use
    batch_update_doc for those. TITLE is recognized as a level-0 terminator;
    SUBTITLE is not a terminator (treated as normal text for boundary purposes).

    Atomicity: one batchUpdate with WriteControl.requiredRevisionId set to the
    revisionId returned by the documents.get call that computed indices. If a
    concurrent edit lands between the read and the write, the API returns 400
    and no changes are applied — caller can retry.

    Preservation (Verified=API-doc-confirmed, Inferred=plausible-from-docs):
      - Comments and suggestions anchored fully OUTSIDE the section keep their
        original anchors (Verified).
      - Comments anchored fully INSIDE the section: in Docs UI display as
        "Original content deleted"; in Drive comments.list response remain
        deleted=false (Inferred — verify with integration test T2).
      - Comments straddling the boundary: behavior depends on the editor's
        anchor reconciliation, which Drive docs explicitly do not guarantee
        ("Anchors are immutable, and their position relative to the content
        of a document cannot be guaranteed between revisions"). Either survive
        on the remaining text or orphan in UI (Inferred — verify T6).
      - Suggestions inside the section are silently dropped (the API has no
        accept/reject for suggestions; deletion is the only path). Notification
        behavior to the suggester is undocumented (Inferred — verify T2).
      - Named ranges in body that span the section: behavior is shrink or split
        per the API's "discontinuous ranges" data model, but the exact reaction
        to a section delete is undocumented (Inferred — verify T5).
      - Body footnote REFERENCES inside the deleted range: the footnote SEGMENT
        is retained in documents.get but invisible in UI (Inferred — verify T7).
        If the API returns 400 instead, the tool needs a pre-check; T7 is gating.
      - Headers, footers, footnote segments outside the body and their named
        ranges are unaffected (Verified — cross-segment delete is not
        expressible in the API).
      - Document ID, sharing, revision history all preserved (Verified).

    Use copy_doc_as_snapshot BEFORE this tool if rollback matters — the snapshot
    is a sibling file the owner can compare with via Tools → Compare Documents.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        heading_text: Visible text of the heading paragraph to match
        new_markdown: Replacement markdown (CommonMark only — tables,
            strikethrough, task lists, autolinks are not supported)
        heading_level: Optional H1-H6 level requirement (1-6)
        match: "first" takes first occurrence; "exact" errors on multiple matches
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
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
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

    Section-end computation same as replace_doc_section_by_heading. No deletion
    — pure insertion at section_end. Nothing is orphaned because nothing is
    deleted. Same multi-tab requirements, same body-only restrictions, same
    WriteControl.requiredRevisionId guard.

    Note: if new_markdown begins with a heading paragraph re-stating the section
    heading, the result is a duplicate heading — caller's responsibility.
    """
```

### 3. `replace_doc_fully_from_markdown` (destructiveHint=True)

```python
@server.tool(
    title="Replace Doc Fully from Markdown",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
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
    excludes the trailing newline (whose deletion is rejected by the API with
    "Deleting the last newline character of a Body").

    Single batchUpdate with WriteControl.requiredRevisionId:
      1. DeleteContentRangeRequest(1, body_protected_end) [skipped if body
         is the empty default — body_protected_end <= 1]
      2. The output of markdown_to_docs_requests(new_markdown, tab_id, 1)

    Document ID survives — distinct from import_to_google_doc, which creates
    a NEW file with a NEW ID.

    Preservation:
      - Body contents wiped: comments orphaned (UI), deleted=false (Drive API,
        Inferred); suggestions silently dropped; body named ranges lost.
      - Headers, footers, footnote SEGMENTS survive — cross-segment delete is
        not expressible in the API (Verified).
      - Body footnote REFERENCES are deleted; the referenced footnote segments
        remain in documents.get but become invisible in UI (Inferred — T7).
      - Section-break header/footer linkages may be lost if the section break
        is deleted; segment contents remain.
      - Doc metadata, sharing, revision history all preserved.
    """
```

### 4. `copy_doc_as_snapshot` (destructiveHint=False)

```python
@server.tool(
    title="Copy Doc as Snapshot",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("copy_doc_as_snapshot", service_type="drive")
@require_google_service("drive", "drive_file")
async def copy_doc_as_snapshot(
    service: Any,
    user_google_email: str,
    document_id: str,
    name: Optional[str] = None,
    folder_id: Optional[str] = None,
    nonce: Optional[str] = None,
) -> str:
    """Create a snapshot copy of a Google Doc as a sibling file.

    Why this tool exists: Drive API revisions for native Docs cannot be pinned
    (keepForever is binary-file-only and silently no-ops on native Docs) or
    named (no `name` field on the Revision resource for native files). Instead,
    this tool creates an independent copy via Drive files.copy — a full-fidelity
    snapshot the owner can compare, revert from, or trash after the edit lands.

    Comments and suggestions are NOT carried to the snapshot. Drive UI's
    "Make a copy" offers checkboxes to copy them; the API path does not. This
    is by design of files.copy, not a limitation of this tool. If the human
    expects thread fidelity, they should use the Drive UI instead.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        name: Snapshot file name. Defaults to "{original_title}.snapshot.{ts}-{nonce}"
            where ts is the caller-supplied compact ISO-8601 timestamp
            (YYYYMMDDTHHMMSSZ; agent supplies because this MCP runs stateless),
            and nonce is a 6-character alphanumeric to defeat retry collisions
            (Drive permits duplicate names — without the nonce a retry creates
            a junk drawer).
        folder_id: Optional parent folder ID. Defaults to the source's parent.
        nonce: Optional 6-character override (rarely needed; agent generates).

    Returns:
        Multi-line confirmation per copy_drive_file convention
        (gdrive/drive_tools.py:2441-2453):
          Snapshot created.
          Snapshot file ID: <id>
          Snapshot link: <webViewLink>
          Source link: <source_webViewLink>
          Paste this on the source doc as a comment for human-visible rollback:
            "Snapshot before agent edit: <snapshot_webViewLink>"

    Notes:
      - supportsAllDrives=True is passed so this works on docs in shared drives
        (matches copy_drive_file at gdrive/drive_tools.py:2387).
      - drive_file is the declared minimum scope; the deployed token must hold
        full drive scope to copy docs NOT created by this app (drive.file alone
        limits to files the app created or that the user opened via Picker).
        The deployed Nango-fed token holds full drive scope (per setup).
      - Snapshot starts a fresh revision history (per-file Drive data model);
        the source's revision history is unaffected (Verified for source;
        Inferred for snapshot — verify T1).
    """
```

**Module placement:** `gdrive/drive_tools.py`.

## Implementation

### Shared helpers (in `gdocs/docs_structure.py`, next to `parse_document_structure`)

```python
def find_heading_range(
    doc: dict,
    heading_text: str,
    heading_level: Optional[int],
    match: Literal["first", "exact"],
    tab_id: Optional[str],
) -> tuple[int, int]:
    """Locate a heading paragraph and the end of its section.

    Walks body.content (for single-tab or top-level body) or the matching
    tab's documentTab.body.content (for multi-tab). Does NOT recurse into
    table cells, footnotes, headers, or footers — those headings are not
    addressable by this helper.

    Match criteria:
      - extract text from paragraph.elements[*].textRun.content
      - strip a single trailing "\n" (heading paragraphs always end with one)
      - case-sensitive, full-string equality against heading_text
      - if heading_level is given, paragraph.paragraphStyle.namedStyleType
        must equal f"HEADING_{heading_level}"
      - paragraphStyle.namedStyleType defaults to "NORMAL_TEXT" when key absent

    Section end:
      - the startIndex of the next paragraph whose namedStyleType is
        HEADING_<= matched_level, or TITLE (SUBTITLE is NOT a terminator)
      - clamped to body_protected_end_index(doc, tab_id) if no such successor

    Multi-tab:
      - if doc has >1 tab and tab_id is None: raises UserInputError with a
        message listing available tab IDs (collected from doc.tabs[*].tabProperties.tabId).
      - if tab_id is given: walks tabs.documentTab.body.content for the
        matching tab; UserInputError if tab_id not found.

    Raises UserInputError on no match, or on (match="exact" AND multiple matches).
    """
```

```python
def body_protected_end_index(doc: dict, tab_id: Optional[str] = None) -> int:
    """Return the largest index that may be passed as DeleteContentRangeRequest.endIndex
    without triggering "Deleting the last newline character of a Body."

    Reads the last structural element of the body (or matching tab body).
    Asserts the last element is a paragraph (per Docs API contract every body
    ends with a paragraph) — raises if not (defends against API contract drift).
    Returns lastElement.endIndex - 1.
    """
```

### Atomic batchUpdate with `WriteControl.requiredRevisionId`

Section-edit tools follow this pattern:

```python
doc = (await asyncio.to_thread(
    service.documents().get(documentId=document_id, includeTabsContent=True).execute
))
revision_id = doc["revisionId"]
section_start, section_end = find_heading_range(doc, ...)
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
```

If a concurrent edit lands between the `get` and the `batchUpdate`, the API returns 400 and nothing is applied. Caller can retry; the tool itself does NOT auto-retry (concurrent edits often mean indices have semantically shifted, not just numerically).

### Why we do NOT need a "stage-and-copy via temp doc" pattern

`markdown_to_docs_requests` emits batchUpdate requests directly with a `start_index` parameter (cursor-threaded, verified by API verifier — `docs_markdown_writer.py:55`). Temp doc unnecessary.

### Atomic-commit plan

Each commit is a green-tests + green-lint checkpoint. Order:

1. `chore: add PLAN.md for section-edit tools` (already committed: `177c7f9`)
2. `chore: revise PLAN.md after round-1 verification` (already committed: `43581fe`)
3. `chore: revise PLAN.md after round-2 verification` (this commit)
4. `feat(gdocs): add find_heading_range + body_protected_end_index helpers`
5. `chore: register placeholder entries in tool_tiers.yaml for upcoming section tools` (so subsequent commits don't break `--tools docs|drive` for operators)
6. `feat(gdocs): add replace_doc_section_by_heading tool`
7. `feat(gdocs): add append_doc_after_heading tool`
8. `feat(gdocs): add replace_doc_fully_from_markdown tool`
9. `feat(gdrive): add copy_doc_as_snapshot tool`
10. `test(gdocs): unit tests for find_heading_range + section tools (mocked)`
11. `test(gdrive): unit test for copy_doc_as_snapshot (mocked)`
12. `docs(README): add 4 new tools to Docs/Drive tables; add preservation matrix appendix`

Each commit:
- passes `uv run pytest tests/ -m "not integration"`
- passes `uv run ruff check .` and `uv run ruff format --check .` (project default: 88 col)
- includes `Claude-Session-Id` + `Resume:` trailers per CLAUDE.md
- one-line subject + body explaining "why" if non-obvious

## Test plan

### Mocked unit tests (FOR UPSTREAM PR — `tests/gdocs/test_section_edit.py` and `tests/gdrive/test_copy_doc_as_snapshot.py`)

Pattern: `from unittest.mock import AsyncMock, Mock`; unwrap tools via the same `_unwrap` helper used in `tests/gdocs/test_advanced_doc_formatting.py:21-26` (copied per repo convention — no shared `conftest.py` for this helper exists today; extracting it is out of scope for this PR).

Tests:
- `find_heading_range` fixtures: no match, single match, multiple match with `match="first"`, multiple with `match="exact"` (errors), nested H1/H2/H3, TITLE as level-0 terminator, SUBTITLE NOT a terminator, heading at end of body (clamped to protected end), heading inside a tab, multi-tab doc with `tab_id=None` (errors), `namedStyleType` field absent (defaults to NORMAL_TEXT), heading text with and without trailing newline.
- `body_protected_end_index` over fixtures: empty body, body with single heading, body with multiple top-level blocks, body ending in a table (last element should still be a paragraph per API contract — if not, helper raises).
- `replace_doc_section_by_heading`: asserts exactly one `batchUpdate` call; asserts `writeControl.requiredRevisionId` is set; asserts request order (delete then inserts); asserts inserts start at `section_start`.
- `append_doc_after_heading`: asserts one `batchUpdate` with inserts only, starting at `section_end`; `requiredRevisionId` set.
- `replace_doc_fully_from_markdown`: asserts delete `(1, body_protected_end)` precedes inserts at index 1; skips delete on near-empty doc; `requiredRevisionId` set.
- `copy_doc_as_snapshot`: asserts `service.files().copy(fileId=..., body={...}, supportsAllDrives=True, fields=...).execute` called with the right `name` and `parents`.

### Integration tests (LOCAL ONLY — NOT in upstream PR)

Per `.github/instructions/general.instructions.md` (do not hit live services in CI), this stays local in `tests/integration/test_section_edit_live.py` marked `pytest.mark.integration`, run with `uv run pytest -m integration ...`.

Setup: create a throwaway test Doc in johntrandall@gmail.com with pre-populated content (heading sections A/B/C with comments and suggestions in known positions, a footnote in section C, a named range straddling section B's boundary).

| Test | Action | Verify |
|---|---|---|
| T1 | `copy_doc_as_snapshot(doc)` | Sibling file with expected name pattern exists; source unchanged; snapshot has fresh revision history |
| T2 | `replace_doc_section_by_heading(doc, "Section B", "## Section B\n\nReplaced.")` | A's comment still anchored; C's suggestion still live; B contents = "Replaced."; record Drive API comment.deleted value for the orphaned B comment; record whether suggester received notification |
| T3 | `append_doc_after_heading(doc, "Section A", "Appended paragraph.")` | A's comment still anchored; new para between A2 and `## Section B` |
| T4 | `replace_doc_fully_from_markdown(doc, "# Wiped\n\nNew body.")` | Doc ID unchanged; record orphaned-comment Drive API state; header/footer survive |
| T5 | Section delete that spans a named range | Record shrink-vs-split observation |
| T6 | Section delete includes a comment anchored across the boundary | Record Drive API state vs UI state |
| T7 (gating) | Section delete includes a footnote reference | Verify either: (a) batchUpdate succeeds and footnote segment retained in `documents.get`, OR (b) 400. If (b), tool needs pre-check; PR is blocked until fix |
| T8 | Multi-tab doc, no `tab_id` arg | Tool raises UserInputError listing tab IDs |
| T9 | Concurrent-edit race: edit doc between `documents.get` and `batchUpdate` | Verify 400 returned; no partial application |

T7 is a **gating test** — if footnote refs cause 400, the tool must pre-scan the section for `footnoteReference` runs and either error early or be redesigned.

### Preservation matrix (canonical — goes in README appendix)

Legend: ✅ = kept • ⚠ = docstring-warning required • ❌ = destroyed • n/a = not applicable.
Confidence: V = Verified against Google docs • I = Inferred (verify via integration test).

| Tool | Comments outside | Comments inside | Comments straddle | Suggestions outside | Suggestions inside | Body named ranges outside | Hdr/ftr/ftnote named ranges | Footnote refs in deleted body | Document ID |
|---|---|---|---|---|---|---|---|---|---|
| `replace_doc_section_by_heading` | ✅ V | ⚠ I (UI orphan; Drive API deleted=false — T2) | ⚠ I (T6) | ✅ V | ⚠ I (silently dropped; notify behavior unverified — T2) | ⚠ I (shrink-or-split — T5) | ✅ V | ⚠ I (segment retained or 400 — T7 gating) | ✅ V |
| `append_doc_after_heading` | ✅ V | n/a | n/a | ✅ V | n/a | ✅ V | ✅ V | n/a | ✅ V |
| `replace_doc_fully_from_markdown` | n/a (body wiped) | ⚠ I (UI orphan; deleted=false — T4) | n/a | n/a (body wiped) | ⚠ I (T4) | ❌ V (body wiped) | ✅ V | ⚠ I (T4) | ✅ V |
| `copy_doc_as_snapshot` | ✅ V (source unchanged) | ✅ V (source unchanged) | n/a | ✅ V | n/a | ✅ V | ✅ V | n/a | ✅ V (source); snapshot has new ID |

## Skill integration

The `google-docs-versioning` skill (committed earlier today, `~/.claude/skills/google-docs-versioning/SKILL.md`) needs amendment:

- Replace `create_version` (which is Apps Script in this MCP) with `copy_doc_as_snapshot`.
- Naming convention: `<original_title>.snapshot.<compact-iso-utc>-<nonce>` (matches the tool's default).
- Snapshot files don't auto-expire — they accumulate in the source's folder until the owner trashes them. Recommend a per-quarter cleanup pass on files matching `*.snapshot.*`.
- Cross-link the 3 section-edit tools as the safe-by-default editing primitives.
- Add the explicit Drive-API-vs-Drive-UI thread-fidelity caveat.

## Backwards compatibility

- No existing tools renamed, removed, or signature-changed.
- 4 new tools live in new file regions; no shared mutable state.
- `find_heading_range` + `body_protected_end_index` are additive helpers in `docs_structure.py`.
- `markdown_to_docs_requests` is consumed unchanged.
- `tool_tiers.yaml` gets 4 new entries under `extended` tier (matches `find_and_replace_doc`/`insert_doc_elements`/`update_paragraph_style` precedent for write tools).

## Upstream PR readiness

Acceptance criteria:

- `uv run pytest tests/ -m "not integration"` passes.
- `uv run ruff check .` and `uv run ruff format --check .` pass.
- README updated: 4 new rows in `Docs` and `Drive` tables matching the `<sub>` formatting + Tier badge convention at lines 768-786 (Drive) and 848-871 (Docs). Preservation matrix added as an appendix.
- Tool docstrings follow Google style with `Args:` / `Returns:` blocks (matches `get_doc_as_markdown` at `gdocs/docs_tools.py:2374`).
- Conventional-commit subjects (`feat(gdocs):`, `feat(gdrive):`, `chore:`, `test(gdocs):`).
- `Allow edits from maintainers` checked on the PR (per `.github/pull_request_template.md:20`).
- PR description includes: motivation, design summary, preservation matrix, local integration-test outputs (especially T7's resolution).

## Deploy plan

### Image build (vendor-fork-deploy)

Dockerfile uses `python:3.11-slim` (`Dockerfile:1`) + `COPY . .` + `uv sync --frozen --no-dev --extra disk` (`Dockerfile:13-16`). Build on Mac via OrbStack per `~/admin-technical/inventories/Compute-Inventory.md:348`:

```bash
cd ~/dev/google_workspace_mcp
git checkout feat/section-edit-tools
SHA=$(git rev-parse --short HEAD)
TAG="v1.21.2-jtr-${SHA}"   # base = upstream pyproject.toml:7 version

docker buildx build --platform linux/amd64 \
  -t localhost/vendor/google-workspace-mcp:${TAG} \
  --load .

skopeo copy --dest-tls-verify=false --format oci \
  docker-daemon:localhost/vendor/google-workspace-mcp:${TAG} \
  docker://umbridge:5050/vendor/google-workspace-mcp:${TAG}
```

`umbridge:5050` because the registry is reachable via MagicDNS from the Mac; the compose files continue to reference `localhost:5050/...` because Synology's docker daemon hits the loopback Zot. `--format oci` because Zot rejects Docker v2 manifests (ADR-097 / Compute-Inventory.md:426-429).

### Compose edits (two stacks)

Edit BOTH:
- `~/dev/portainer-stacks/google-workspace-mcp/docker-compose.yml` — change `image:` to `localhost:5050/vendor/google-workspace-mcp:v1.21.2-jtr-<sha>`
- `~/dev/portainer-stacks/google-workspace-mcp-frisbee/docker-compose.yml` — same

```bash
cd ~/dev/portainer-stacks
# edit both compose files
git add google-workspace-mcp/docker-compose.yml google-workspace-mcp-frisbee/docker-compose.yml
git commit -m "deploy(google-workspace-mcp): v1.21.2-jtr-<sha> — section-edit tools"
git push forgejo-umbridge main
```

(Push identity: precedent from `git log` is `John Randall <john@johnrandall.com>` — match precedent unless the operator decides otherwise. `infra-agent` identity not used here.)

### Portainer git-redeploy (per `portainer-stack-operations` skill)

Stack IDs to look up before redeploy:
- `google-workspace-mcp` (john, port 8900) — ID = TODO look up via `GET /api/stacks?filters=...`
- `google-workspace-mcp-frisbee` (port 8931) — ID = 260 (per inventory)

For each stack:
1. `GET /api/stacks/{id}` → parse the response, capture the `Env` array exactly as returned.
2. `PUT /api/stacks/{id}/git/redeploy` with body:
   ```json
   {
     "Env": <preserved-env-array>,
     "RepositoryAuthentication": true,
     "RepositoryUsername": "john",
     "RepositoryPassword": "<op:read 'op://JRVIS Infra/Forgejo - Portainer GitOps Token/credential'>",
     "RepositoryReferenceName": "refs/heads/main",
     "Prune": true,
     "PullImage": true
   }
   ```

`Env` MUST be the literal preserved array — empty/omitted wipes it (per `feedback-portainer-env-wipe`).

### Token / scope check (pre-deploy)

The deployed token must include `https://www.googleapis.com/auth/drive` (full) for `files.copy` on arbitrary user-owned docs and `.../auth/documents` for `batchUpdate`. Verify per stack:

```bash
ssh infra-agent@umbridge \
  "docker exec google-workspace-mcp-john \
   cat /root/.google_workspace_mcp/credentials/johntrandall@gmail.com.json" \
  | jq -r '.scopes[]'
```

(Container-internal path — avoids the Synology `/volume1/@docker/volumes/...` host-side resolution.)

If `https://www.googleapis.com/auth/drive` is absent: re-auth via Nango (NOT via local `uvx workspace-mcp` — that writes to a different store and won't affect the running container):

1. Update the OAuth client (or its consent screen) in GCP to include the full `drive` scope.
2. In the Nango UI at `https://umbridge.flicker-duckbill.ts.net:3004`, re-authorize the `google-workspace-john` and `google-workspace-frisbee` connections with the broader scope set (this triggers a new OAuth flow that issues a refresh token bound to the new scopes).
3. Force the token feeder to fetch: `docker restart google-workspace-token-feeder` (and the frisbee equivalent).
4. Re-verify scopes via the docker exec cat command above.

### Smoke test (post-deploy)

Lives in `~/admin-technical/setup/synology/google-workspace-mcp/scripts/smoke_test_tool_list.py` (NOT in the upstream fork). Uses the `mcp` Python SDK; run via `uvx --with mcp python smoke_test_tool_list.py http://umbridge:8900/mcp` (and again for 8931).

Smoke test should:
1. Connect via Streamable HTTP, initialize session, call `tools/list`.
2. Assert the 4 expected tool names are present.
3. Call `copy_doc_as_snapshot` against a known throwaway test doc — verifies runtime health (not just registration).
4. Print PASS/FAIL summary.

If smoke test FAILS on john:
- Read `docker logs google-workspace-mcp-john --tail 200` for import errors.
- If fixable: amend, rebuild image with new SHA, re-tag, re-push, edit compose, re-push to portainer-stacks, re-redeploy john only.
- If not fixable in 15 min: revert compose for john only (`git revert <commit> -- google-workspace-mcp/docker-compose.yml`), push, redeploy john with the old tag. Do NOT proceed to frisbee.

If smoke test PASSES on john but FAILS on frisbee: same rollback for frisbee only. Leaves john on new code, frisbee on old. Acceptable short-term split state.

### Order

1. Build + push image
2. Verify scopes per stack (pre-deploy)
3. Re-auth via Nango if scopes lacking; wait for token feeder cycle
4. Edit + push compose for BOTH stacks (single commit)
5. Redeploy john → smoke test john
6. Redeploy frisbee → smoke test frisbee

## Risks + open questions (FOR ROUND-3 VERIFIERS)

Round-2 open questions resolved:
1. ✅ `extended` tier confirmed for all 4 tools (matches existing write-tool precedent).
2. ✅ `copy_doc_as_snapshot` default name uses original title via a `files.get` round-trip + nonce.
3. ✅ Case-sensitive matching kept; no `case_sensitive` parameter added (YAGNI).
4. ✅ Atomic-batch ordering is a unit test + a code comment; not a runtime guard.
5. ✅ Preservation matrix lives in README appendix.

New (smaller) open questions for round 3:
1. The smoke test calls `copy_doc_as_snapshot` against a "known throwaway test doc" — where's its document_id stored? Plain text in the script? 1Password? Hardcoded with a comment?
2. T7 footnote-reference test: if the test reveals a 400, what's the redesigned tool's API? Pre-check + raise vs auto-strip-footnote-refs from the section?
3. The PR includes 9 commits (find_heading_range helper, tool_tiers.yaml placeholder, 4 tools, 2 test files, README). Should commits 6-9 collapse to a single feature commit to make the upstream review more reviewable? Vendor-fork-deploy convention says "one focused commit (or a tight handful)" — 4 separate tool commits arguably too granular.
