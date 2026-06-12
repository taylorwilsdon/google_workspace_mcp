# PLAN — Section-Addressable Markdown Editing for Google Docs

Branch: `feat/section-edit-tools`
Status: DRAFT v2 — revised after round-1 verification.

## Round-1 verification highlights folded in

Four adversarial verifiers (API correctness, preservation matrix, upstream style, deploy path) found one fatal design flaw and many precision gaps in v1. v2 changes:

- **Tool #4 redesigned.** `pin_doc_revision` is non-functional — Drive API's `keepForever` is binary-file-only on native Docs (silently no-ops), and the Revision resource has no `name` field for native files. Replaced with `copy_doc_as_snapshot` using Drive `files.copy` — a real, supported, GC-immune snapshot.
- **Off-by-one fixed.** `(1, end - 1)` would 400 on the "deleting last newline" rule. Corrected to compute the protected trailing-newline position from the trailing paragraph element, with a defensive clamp.
- **Multi-tab handling.** Plan explicitly rejects multi-tab docs without a `tab_id`, with a documented escape valve (call `inspect_doc_structure` first to list tab IDs).
- **TITLE/SUBTITLE as section boundaries** is documented; out of scope for matching, in scope as section terminators.
- **Preservation matrix expanded** with three new columns: straddle-comments, footnote-orphaning, body-vs-non-body-segment named ranges.
- **Full decorator stacks specified per tool.** Service-param order corrected to match `get_doc_as_markdown`. `destructiveHint` / `idempotentHint` resolved.
- **`tool_tiers.yaml` registration** added as required commit (without it, the deployed `--tools docs|drive` flag wouldn't expose them).
- **`find_heading_range` placed in `gdocs/docs_structure.py`** matching the project's domain-module split.
- **Tool renamed:** `append_after_heading` → `append_doc_after_heading` for `_doc_` infix consistency.
- **Tests:** upstream PR carries mocked unit tests only (uses `unittest.mock` + `_unwrap()` helper per `tests/gdocs/test_advanced_doc_formatting.py`); live-Doc integration tests stay local-only.
- **Image tag convention** chosen: `v2.1.0-jtr-<sha>`. Two-stack compose edit + git push to `infra/portainer-stacks` is now an explicit step.
- **Portainer env-preservation** required on redeploy per `feedback-portainer-env-wipe`.
- **Smoke-test verb** specified: MCP `tools/list` over Streamable HTTP via a small Python client.

## Context

The Docs API addresses edits by `(startIndex, endIndex)` character offsets in a structural-element tree. Agents struggle with this — computing the right indices requires reading the doc, walking the element tree, and threading paragraph/text-run/inline-style boundaries. Typical agent failures:

- Falling back to `import_to_google_doc` against an existing doc (which creates a NEW file with a NEW ID, breaking every external link), OR
- Issuing dozens of `batch_update_doc` calls with hand-computed indices that drift after the first insertion (each `InsertTextRequest` shifts every subsequent index).

This MCP already has the two primitives needed:

1. **`get_doc_as_markdown`** — pulls current state with comment context (`gdocs/docs_tools.py:2364`)
2. **`markdown_to_docs_requests(markdown_text, tab_id, start_index)`** — converts CommonMark to a list of Docs API batchUpdate request dicts ready to apply at any start index (`gdocs/docs_markdown_writer.py:23`)

What's missing is **section-addressable editing**: tools that locate a target range by **heading text** (or doc-wide), delete the range, and insert markdown-converted requests at the deleted slot — all in one atomic `batchUpdate`. Plus a real safety net: **snapshot-by-copy** since Drive revisions can't be named or pinned via API for native Docs.

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
@handle_http_errors("replace_doc_section_by_heading", is_read_only=False, service_type="docs")
@require_google_service("docs", "docs_write")
async def replace_doc_section_by_heading(
    docs_service: Any,
    user_google_email: str,
    document_id: str,
    heading_text: str,
    new_markdown: str,
    heading_level: Optional[int] = None,
    match: Literal["first", "exact"] = "first",
    tab_id: Optional[str] = None,
) -> str:
    """Replace a heading-delimited section's contents with new markdown.

    Locates the heading whose visible text equals heading_text (case-sensitive,
    full string). If heading_level is given, also requires that level.
    The "section" runs from the heading paragraph's startIndex through (exclusive)
    the next paragraph whose namedStyleType is a heading at equal-or-shallower
    level (or TITLE/SUBTITLE) — or the body's protected trailing newline boundary
    if no such heading follows.

    Atomic: deletes the section range and inserts new_markdown converted by
    markdown_to_docs_requests, in a single batchUpdate.

    Preservation: comments and suggestions anchored OUTSIDE the section keep
    their original anchors. Comments anchored INSIDE become orphaned (visible
    in Docs UI as attached to "deleted text"; in Drive API they still return
    deleted=false — see preservation matrix). Suggestions inside are silently
    dropped with no audit trail to the suggester. Use copy_doc_as_snapshot
    BEFORE calling this tool if rollback matters.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        heading_text: Visible text of the heading paragraph to match
        new_markdown: Replacement markdown (CommonMark only; tables, strike, task lists not supported)
        heading_level: Optional H1-H6 level requirement (1-6)
        match: "first" takes first occurrence; "exact" errors on multiple matches
        tab_id: Tab ID for multi-tab docs. REQUIRED if doc has >1 tab; raises UserInputError otherwise.

    Returns:
        Human-readable single-line confirmation including character delta, request count,
        the new range bounds, and the doc's webViewLink (matches upstream convention,
        see update_paragraph_style at docs_tools.py:2341).
    """
```

### 2. `append_doc_after_heading` (destructiveHint=False)

```python
@server.tool(
    title="Append After Heading",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("append_doc_after_heading", is_read_only=False, service_type="docs")
@require_google_service("docs", "docs_write")
async def append_doc_after_heading(
    docs_service: Any,
    user_google_email: str,
    document_id: str,
    heading_text: str,
    new_markdown: str,
    heading_level: Optional[int] = None,
    match: Literal["first", "exact"] = "first",
    tab_id: Optional[str] = None,
) -> str:
    """Insert markdown at the end of a heading-delimited section.

    Section-end computation same as replace_doc_section_by_heading.
    No deletion — pure insertion at section_end. Nothing is orphaned.

    Note: if new_markdown begins with the same heading, the result is a
    duplicate heading in the doc — caller's responsibility.
    """
```

### 3. `replace_doc_fully_from_markdown` (destructiveHint=True)

```python
@server.tool(
    title="Replace Doc Fully From Markdown",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("replace_doc_fully_from_markdown", is_read_only=False, service_type="docs")
@require_google_service("docs", "docs_write")
async def replace_doc_fully_from_markdown(
    docs_service: Any,
    user_google_email: str,
    document_id: str,
    new_markdown: str,
    tab_id: Optional[str] = None,
) -> str:
    """Replace the entire body of a Doc with new markdown.

    Computes the body's protected-trailing-newline index from the last
    structural element (NOT `body.endIndex - 1`, which would 400 with
    "Deleting the last newline character of a Body"). Deletes (1, body_protected_end)
    where body_protected_end excludes the trailing newline, then inserts
    markdown_to_docs_requests(new_markdown, start_index=1).

    Single batchUpdate. Document ID survives — distinct from import_to_google_doc
    which creates a NEW file.

    Preservation: body contents wiped (comments orphaned, suggestions silently
    dropped, body named ranges lost). Headers, footers, footnote SEGMENTS survive
    (cross-segment delete is not expressible in API). Body footnote REFERENCES
    are deleted — the referenced footnote segments remain in API but become
    invisible in UI (see matrix). Doc-level metadata, sharing, revision history
    all preserved.

    Defensive: if body has only the empty-paragraph default (no content to delete),
    skips the delete request.
    """
```

### 4. `copy_doc_as_snapshot` (destructiveHint=False) — REPLACES dead pin_doc_revision

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
@handle_http_errors("copy_doc_as_snapshot", is_read_only=False, service_type="drive")
@require_google_service("drive", "drive_file")
async def copy_doc_as_snapshot(
    drive_service: Any,
    user_google_email: str,
    document_id: str,
    name: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> str:
    """Create a snapshot copy of a Google Doc as a sibling file.

    Drive API revisions for native Docs cannot be pinned via keepForever
    (binary-only) or named (no `name` field on the Revision resource for
    native files). Instead, this tool creates an independent copy via Drive
    files.copy — a full-fidelity snapshot the owner can compare, revert from,
    or delete after the edit is accepted.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        name: Snapshot file name; defaults to f"{original}.snapshot.{utc_iso8601_safe}"
              where original is the source Doc's title and the timestamp is
              caller-provided ISO 8601 with colons replaced (Drive disallows them).
              Time source is the agent's UTC clock at the moment of the call,
              passed via the args (this MCP runs in a stateless container).
        folder_id: Optional parent folder; defaults to the source's folder.

    Returns:
        Single-line: snapshot file ID, snapshot file webViewLink, source webViewLink,
        and a copy-pastable comment string the caller can paste on the source doc
        ("Snapshot before agent edit: <webViewLink>").

    Preservation: source is not modified; comments and suggestions on the source
    DO NOT copy to the snapshot (Drive files.copy copies content, not threads).
    Snapshot is a frozen, independent file under the same owner.
    """
```

**Why this design beats pin_doc_revision:**
- Works (vs. silent no-op on native Docs)
- Owner-visible in their Drive (vs. buried in revision history)
- Owner can compare via Tools → Compare Documents (renders diff as suggestions in a third file)
- Survives indefinitely; no 30-day / 100-rev GC concern
- Authorization is `drive.file` (file the app created — the snapshot), well within the token's existing scopes
- One-way copy is deterministic; no merge race like the proposed revision PATCH had

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

    Returns (section_start, section_end) such that:
      - section_start = the matched heading paragraph's startIndex
      - section_end = the startIndex of the next paragraph whose
        paragraphStyle.namedStyleType matches HEADING_<= matched_level
        OR is TITLE/SUBTITLE; or the body's protected-trailing-newline
        position if no such successor.

    Tab handling:
      - If the doc has >1 tab and tab_id is None, raises UserInputError
        with a message listing the available tab IDs.
      - If tab_id is given, walks the matching tab's documentTab.body.content.
      - parse_document_structure as currently written walks only body.content;
        find_heading_range walks tabs[*] directly (helper-local).

    namedStyleType detection:
      - Defaults to "NORMAL_TEXT" when key absent (per API contract).

    Raises UserInputError on no match, or on (match="exact" AND multiple matches).
    """
```

```python
def body_protected_end_index(doc: dict, tab_id: Optional[str] = None) -> int:
    """Return the largest index that may be passed as DeleteContentRangeRequest.endIndex
    without triggering "Deleting the last newline character of a Body."

    Implementation: the body (or tab body) consists of structural elements with
    startIndex/endIndex. The last element is always a paragraph; its endIndex
    is exclusive and corresponds to one past the protected trailing newline.
    Return endIndex - 1.
    """
```

### Why we do NOT need the "stage-and-copy via temp doc" pattern

`markdown_to_docs_requests` emits batchUpdate requests directly with a `start_index` parameter (cursor-threaded, verified by API verifier — `docs_markdown_writer.py:55`). Temp doc unnecessary.

### Atomic-batch ordering

Within one `batchUpdate`, requests apply sequentially against the running document state. Our pattern — delete then insert at `section_start` — is correct because:
1. Delete (request[0]) collapses the section, shifting subsequent indices left.
2. First insert (request[1]) targets `section_start`, which is now valid in the post-delete state.
3. `markdown_to_docs_requests` emits subsequent requests with indices `section_start + cumulative_len`, matching the document state at the moment each applies.

**Required:** a code comment at the call site asserting "delete MUST precede inserts in the request list," and a unit test asserting the request order.

### TITLE/SUBTITLE handling

`find_heading_range` treats TITLE and SUBTITLE as level-0 (terminate the section regardless of matched level). Documented in the helper's docstring. Matching against TITLE/SUBTITLE is NOT supported in `heading_text`/`heading_level` (a heading match requires `HEADING_N`); a future PR can add `style: Literal["heading","title","subtitle"]` if needed.

### Atomic-commit plan

Each commit is a green-tests + green-lint checkpoint. Order:

1. `chore: add PLAN.md for section-edit tools` (already committed: `177c7f9`)
2. `chore: revise PLAN.md after round-1 verification` (this commit)
3. `feat(gdocs): add find_heading_range + body_protected_end_index helpers`
4. `feat(gdocs): add replace_doc_section_by_heading tool`
5. `feat(gdocs): add append_doc_after_heading tool`
6. `feat(gdocs): add replace_doc_fully_from_markdown tool`
7. `feat(gdrive): add copy_doc_as_snapshot tool`
8. `chore: register new tools in tool_tiers.yaml under extended tier`
9. `test(gdocs): unit tests for find_heading_range + section tools (mocked)`
10. `test(gdrive): unit test for copy_doc_as_snapshot (mocked)`
11. `docs(README): add 4 new tools to Docs/Drive tables; add preservation matrix appendix`

Each commit:
- passes `uv run pytest tests/` (excluding `-m integration`)
- passes `uv run ruff check .` and `uv run black --check .` (120 col)
- includes the `Claude-Session-Id` + `Resume:` trailers per CLAUDE.md
- one-line subject + body explaining "why" if non-obvious

## Test plan

### Mocked unit tests (FOR UPSTREAM PR — `tests/gdocs/` and `tests/gdrive/`)

Pattern: `from unittest.mock import AsyncMock, Mock`; unwrap tools via `fn = tool.fn if hasattr(tool, "fn") else tool; while hasattr(fn, "__wrapped__"): fn = fn.__wrapped__` (per `tests/gdocs/test_advanced_doc_formatting.py:21-26`).

- `find_heading_range` fixtures: no match, single match, multiple match with `match="first"`, multiple with `match="exact"` (errors), nested H1/H2/H3, heading at end of body (clamped to protected end), heading inside a tab, multi-tab doc with `tab_id=None` (errors), `namedStyleType` field absent (defaults to NORMAL_TEXT).
- `body_protected_end_index` over fixtures: empty body, body with single heading, body with multiple top-level blocks.
- `replace_doc_section_by_heading`: asserts exactly one `batchUpdate` call; asserts request order (delete then inserts); asserts inserts start at `section_start`.
- `append_doc_after_heading`: asserts one `batchUpdate` with inserts only, starting at `section_end`.
- `replace_doc_fully_from_markdown`: asserts delete `(1, body_protected_end)` precedes inserts at index 1; skips delete on near-empty doc.
- `copy_doc_as_snapshot`: asserts `drive_service.files().copy(fileId=..., body={...}).execute` called with the right name + parent.

### Integration test (LOCAL ONLY — NOT in upstream PR — `tests/integration/test_section_edit_live.py` marked `pytest.mark.integration`)

Per `.github/instructions/general.instructions.md` (do not hit live services in CI), this test stays local, marked with `pytest.mark.integration`, and is run with `uv run pytest -m integration tests/integration/test_section_edit_live.py`.

Create a throwaway test Doc in johntrandall@gmail.com. Pre-populate:

```
# Test Doc

## Section A

Paragraph A1. <comment anchored here>

Paragraph A2.

## Section B

Paragraph B1.

## Section C

Paragraph C1. <suggestion: replace "C1" with "C-one">
```

| Test | Action | Verify |
|---|---|---|
| T1 | `copy_doc_as_snapshot(doc)` | Sibling file exists with timestamped name; source unchanged |
| T2 | `replace_doc_section_by_heading(doc, "Section B", "## Section B\n\nReplaced.")` | A's comment still anchored; C's suggestion still live; B contents = "Replaced." |
| T3 | `append_doc_after_heading(doc, "Section A", "Appended paragraph.")` | A's comment still anchored; new para between A2 and `## Section B` |
| T4 | `replace_doc_fully_from_markdown(doc, "# Wiped\n\nNew body.")` | Doc ID unchanged; all body comments orphaned in UI but still `deleted=false` in Drive API |
| T5 | Section delete that spans a named range | Verify shrink-vs-split behavior; record observed result |
| T6 | Section delete includes a comment anchored across the boundary | Verify Drive API state vs UI state; record observed result |
| T7 | Section delete includes a footnote reference | Verify footnote segment retained in `documents.get`, invisible in UI |
| T8 | Multi-tab doc, no `tab_id` arg | Tool raises UserInputError listing tab IDs |

### Preservation matrix (canonical — goes in README appendix)

| Tool | Comments anchored fully outside | Comments anchored fully inside | Comments straddling boundary | Suggestions outside | Suggestions inside | Body named ranges outside | Header/footer/footnote named ranges | Footnote refs in deleted body | Document ID |
|---|---|---|---|---|---|---|---|---|---|
| `replace_doc_section_by_heading` | ✅ kept | ⚠ orphaned in UI; `deleted=false` in Drive API | ✅ kept w/ shrunk anchor | ✅ kept | ⚠ silently dropped (no notify to suggester) | ✅ kept | ✅ kept | ⚠ segment retained in API, UI-invisible | ✅ unchanged |
| `append_doc_after_heading` | ✅ kept | n/a | n/a | ✅ kept | n/a | ✅ kept | ✅ kept | n/a | ✅ unchanged |
| `replace_doc_fully_from_markdown` | n/a (all wiped) | ⚠ all orphaned in UI; `deleted=false` in Drive API | n/a | n/a (all wiped) | ⚠ all silently dropped | ❌ all lost | ✅ kept | ⚠ all segments retained in API, UI-invisible | ✅ unchanged |
| `copy_doc_as_snapshot` | ✅ source unchanged; snapshot has no comments | ✅ source unchanged | n/a | ✅ source unchanged; snapshot has no suggestions | n/a | ✅ source unchanged | ✅ source unchanged | n/a | ✅ unchanged (snapshot has its own new ID) |

Legend: ✅ kept • ⚠ requires-docstring-warning • ❌ destroyed • n/a not applicable to this op.

## Skill integration

The `google-docs-versioning` skill (committed earlier today, `~/.claude/skills/google-docs-versioning/SKILL.md`) needs amendment:

- Replace references to `create_version` (which is Apps Script in this MCP) with `copy_doc_as_snapshot`.
- Adjust naming convention from `pre-agent-edit-<iso>-<purpose>` to `<original>.snapshot.<iso>-<purpose>` (snapshot files live in the same folder as the source; the source's title is the natural prefix).
- Update "GC" section: snapshot files don't auto-expire — they accumulate in the source's folder until the owner trashes them. Recommend a per-quarter cleanup pass on files matching `*.snapshot.*`.
- Cross-link the 3 section-edit tools as the safe-by-default editing primitives.
- Add the explicit Drive-API-vs-UI orphan-comment caveat.

## Backwards compatibility

- No existing tools renamed, removed, or signature-changed.
- 4 new tools live in new file regions; no shared mutable state.
- `find_heading_range` + `body_protected_end_index` are additive helpers in `docs_structure.py`.
- `markdown_to_docs_requests` is consumed unchanged.
- `tool_tiers.yaml` gets 4 new entries under `extended` tier (rationale: the section tools are higher-power than core read tools; `complete` tier is for niche/expensive ops).

## Upstream PR readiness

Acceptance criteria:

- All `uv run pytest tests/ -m "not integration"` pass.
- `uv run ruff check .` and `uv run black --check .` pass.
- README updated: 4 new rows in `Docs` and `Drive` tables with the `<sub>` formatting + Tier badge (matches `gdocs/docs_tools.py` table at README.md:848-871 pattern). Preservation matrix added as an appendix.
- Tool docstrings follow Google style with `Args:` / `Returns:` blocks (matches `get_doc_as_markdown` at `gdocs/docs_tools.py:2374`).
- Conventional-commit subjects (`feat(gdocs):`, `test(gdocs):`).
- `Allow edits from maintainers` checked on the PR.
- PR description includes: motivation, design summary, preservation matrix, local integration-test outputs.

## Deploy plan

### Image build (vendor-fork-deploy)

Dockerfile uses `COPY . .` then `uv sync` from local source (verified: `Dockerfile:13-16`), so whatever branch is checked out at build time runs. No private wheel needed.

1. Build on Umbridge from `feat/section-edit-tools` checkout:
   - `cd ~/google_workspace_mcp && git fetch && git checkout feat/section-edit-tools`
   - `SHA=$(git rev-parse --short HEAD)`
   - `docker build -t localhost:5050/vendor/google-workspace-mcp:v2.1.0-jtr-$SHA .`
   - `docker push localhost:5050/vendor/google-workspace-mcp:v2.1.0-jtr-$SHA`

2. Update compose files in `~/dev/portainer-stacks/`:
   - `google-workspace-mcp/docker-compose.yml`: change `image:` to the new tag
   - `google-workspace-mcp-frisbee/docker-compose.yml`: same
   - `git commit -m "deploy(google-workspace-mcp): v2.1.0-jtr-<sha> — section-edit tools"`
   - `git push forgejo-umbridge main`

3. Redeploy each Portainer stack via API, preserving env per `feedback-portainer-env-wipe`:
   - `GET /api/stacks/{id}` to read current `Env` array
   - `PUT /api/stacks/{id}/git/redeploy` passing back the `Env` array unchanged
   - Stack IDs: john = (look up from inventory MCP entry), frisbee = 260

### Smoke test (post-deploy)

Tool-list MCP `tools/list` over Streamable HTTP. Write a small Python client using the `mcp` SDK:

```python
# scripts/smoke_test_tool_list.py
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

async def main():
    async with streamablehttp_client("http://umbridge:8900/mcp") as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            expected = {
                "replace_doc_section_by_heading",
                "append_doc_after_heading",
                "replace_doc_fully_from_markdown",
                "copy_doc_as_snapshot",
            }
            present = {t.name for t in tools}
            missing = expected - present
            assert not missing, f"Missing tools: {missing}"
            print(f"OK: all 4 tools present in {len(tools)} total")

asyncio.run(main())
```

Run this against john (`umbridge:8900`) and frisbee (`umbridge:8931`) after each redeploy.

### Token / scope check (pre-deploy)

Verify the existing OAuth token holds the `drive` scope (full, not `.readonly`). On Umbridge:

```bash
sudo cat /var/lib/docker/volumes/google-workspace-mcp-data/_data/johntrandall@gmail.com.json | jq -r '.scopes[]'
```

Required scopes: `https://www.googleapis.com/auth/drive` (for `files.copy`) and `https://www.googleapis.com/auth/documents` (for `batchUpdate`). Same for `google-workspace-mcp-frisbee-data`. If absent, re-auth before deploy.

## Risks + open questions (FOR ROUND-2 VERIFIERS)

Round 1's open questions resolved:
1. ✅ Drive API CANNOT name a Doc revision. Replaced approach with files.copy.
2. ✅ `match="first"` chosen as default; explicit choice required via parameter.
3. ✅ `tab_id` required for multi-tab docs; UserInputError lists available tab IDs.
4. ✅ Multi-style heading matching (TITLE/SUBTITLE) deferred; HEADING_N only for matching; TITLE/SUBTITLE recognized as section terminators.
5. ✅ Converter limited to CommonMark; documented in tool docstring.
6. ✅ Image tag `v2.1.0-jtr-<sha>`; two-stack compose edit explicit.
7. ✅ Suggestion-mode interaction documented as silent drop in matrix.

New open questions for round 2 to challenge:
1. Is `extended` the right tier for these tools, or should they be `core`? Argument for core: agents will reach for them constantly. Argument for extended: they're powerful/destructive, opt-in feels right.
2. The `name` arg on `copy_doc_as_snapshot` — when the caller passes `None`, we generate `f"{original}.snapshot.{ts}"`. Source title comes from a `files.get` round-trip. Acceptable? Alternative: leave name unset and let Drive default to "Copy of X".
3. Does `find_heading_range` need a `case_sensitive: bool = True` parameter? Most docs use Title Case headings; agents might pass lowercase. Argument for True default: predictable matching. Argument for adding the flag: convenience.
4. The atomic-batch ordering assertion (delete-before-inserts) is currently a runtime comment + unit test. Should it be a runtime guard (assert in code)? Reviewer may ask.
5. Does the README appendix or a separate `docs/` markdown file own the preservation matrix?
