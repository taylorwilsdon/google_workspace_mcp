# PLAN — Section-Addressable Markdown Editing for Google Docs

Branch: `feat/section-edit-tools`
Status: DRAFT — pending iterative verification.

## Context

The Docs API addresses edits by `(startIndex, endIndex)` character offsets in a structural-element tree. Agents struggle with this — computing the right indices for a target range requires reading the doc structure, walking the element tree, summing element lengths, and threading paragraph/text-run/inline-style boundaries correctly. The typical agent failure mode is either:

- Falling back to `import_to_google_doc` which **destroys the entire body** (wiping comments, suggestions, named-range anchors), OR
- Issuing dozens of `batch_update_doc` calls with hand-computed indices that drift after the first insertion (each `InsertTextRequest` shifts every subsequent index).

This MCP already has the two primitives needed to bridge the gap:

1. **`get_doc_as_markdown`** — pulls current state with comment context (gdocs/docs_tools.py:2364)
2. **`markdown_to_docs_requests(markdown_text, tab_id, start_index)`** — converts CommonMark to a list of Docs API batchUpdate request dicts ready to apply at any start index (gdocs/docs_markdown_writer.py:23)

What's missing is **section-addressable editing**: tools that locate a target range by **heading text** (or doc-wide), delete the range, and insert markdown-converted requests at the deleted slot — all in one atomic `batchUpdate`. That's what this PR adds.

## Scope — 4 new MCP tools

### 1. `replace_doc_section_by_heading`

**Signature:**
```python
async def replace_doc_section_by_heading(
    docs_service, drive_service,
    user_google_email: str,
    document_id: str,
    heading_text: str,
    new_markdown: str,
    heading_level: Optional[int] = None,
    match: Literal["first", "exact"] = "first",
    tab_id: Optional[str] = None,
) -> str
```

**Semantics:**
- Locate the heading whose visible text matches `heading_text` (case-sensitive, full-string). If `heading_level` is given, also require that level. `match="exact"` errors if the heading text appears more than once; `match="first"` takes the first occurrence (logged).
- The "section" is everything from the heading paragraph's `startIndex` through (exclusive) the next paragraph with `namedStyleType` matching a heading of equal-or-higher level, or end of body if none.
- Emit one `batchUpdate` call combining:
  1. `DeleteContentRangeRequest` for the section range
  2. The output of `markdown_to_docs_requests(new_markdown, tab_id=tab_id, start_index=section_start)`
- Return a structured success message including: number of characters replaced, number of requests applied, range bounds before/after, and the pinned-revision ID if `auto_pin=True` (deferred — see Skill Integration).

**Preservation guarantees:**
- Comments anchored to ranges OUTSIDE `(section_start, section_end)` keep their original anchors.
- Suggestions outside the section stay live.
- Named ranges spanning the section shrink; named ranges fully outside are unchanged.
- Headers, footers, footnotes — all unchanged.

**Loss (inherent to Docs API, not avoidable):**
- Comments anchored INSIDE the deleted section become orphaned ("attached to deleted text" in Docs UI).
- Suggestions inside the deleted section are deleted along with the text.

### 2. `append_after_heading`

**Signature:**
```python
async def append_after_heading(
    docs_service,
    user_google_email: str,
    document_id: str,
    heading_text: str,
    new_markdown: str,
    heading_level: Optional[int] = None,
    match: Literal["first", "exact"] = "first",
    tab_id: Optional[str] = None,
) -> str
```

**Semantics:**
- Locate the heading the same way as #1.
- Compute the section end (next equal-or-higher heading, or body end).
- Emit one `batchUpdate` with the requests from `markdown_to_docs_requests(new_markdown, tab_id=tab_id, start_index=section_end)`.
- No delete — pure insertion at the end of the section.

**Preservation:** Everything outside the inserted region is unchanged; no orphaning happens because nothing is deleted.

### 3. `replace_doc_fully_from_markdown`

**Signature:**
```python
async def replace_doc_fully_from_markdown(
    docs_service,
    user_google_email: str,
    document_id: str,
    new_markdown: str,
    tab_id: Optional[str] = None,
) -> str
```

**Semantics:**
- Read the document; compute the body's end index.
- Single `batchUpdate`:
  1. `DeleteContentRangeRequest` for `(1, end - 1)`
  2. Requests from `markdown_to_docs_requests(new_markdown, tab_id=tab_id, start_index=1)`
- The destructive option. Useful for agent-owned status reports and similar where there are no live collaborators or comments to preserve.

**Distinction from existing `import_to_google_doc`:** that tool re-imports through Drive's converter (going via uploaded markdown), which creates a NEW file. This tool stays inside the existing file, preserving file ID, sharing, comments anchored to retained ranges (there are none here, since we delete it all — but the file ID survives, which matters for links and named-version pins).

**Why include this even though it's "destructive":** giving agents a clear named tool for "I want to replace this whole doc, I know what I'm doing" prevents them from reaching for `import_to_google_doc` (which creates a new file with a new ID, breaking every external link to the doc).

### 4. `pin_doc_revision`

**Signature:**
```python
async def pin_doc_revision(
    drive_service,
    user_google_email: str,
    document_id: str,
    name: Optional[str] = None,
) -> str
```

**Semantics:**
- List revisions, get the latest revision ID.
- `revisions.update(fileId=document_id, revisionId=latest_id, body={"keepForever": True})` to pin it past the 30-day / 100-rev auto-GC.
- If `name` is provided AND the Drive API actually supports a `name` field on Doc/Sheet/Slides revisions (open question — must verify), set it. Otherwise return the revision's `id` + `modifiedTime` as the identifier.
- Return: revision ID, modified timestamp, keepForever=True, name (if set), and a string the caller can paste into a doc comment ("revert via File → Version history → revision dated 2026-06-12 18:14:23 UTC").

**Why this is the 4th tool (added during plan drafting):** The MCP's existing `create_version`/`list_versions`/`get_version` are scoped to Apps Script projects, not Drive files. The `google-docs-versioning` skill committed earlier today assumes a Drive-file-scoped pin tool exists. It doesn't. This adds it.

**Module placement:** `gdrive/drive_tools.py` (not gdocs/) — revisions are a Drive API concern that works for any native Google file.

## Implementation

### Shared helpers (added to `gdocs/docs_helpers.py` or a new `gdocs/section_finder.py`)

```python
def find_heading_range(
    doc: dict,
    heading_text: str,
    heading_level: Optional[int],
    match: Literal["first", "exact"],
    tab_id: Optional[str],
) -> tuple[int, int]:
    """Return (section_start, section_end) for the heading matching the criteria.
    
    section_start is the heading paragraph's startIndex.
    section_end is the first index AT a same-or-higher heading paragraph,
    or the body's end-1 index if no such heading follows.
    
    Walks the structural-element tree via parse_document_structure (existing helper).
    Handles multi-tab docs by scoping to tab_id when provided.
    Raises UserInputError on no-match or (match="exact" AND multiple matches).
    """
```

### Why we do NOT need the "stage-and-copy via temp doc" pattern

Original plan considered creating a temp doc, importing the markdown there, then copying structural elements back. The existing `markdown_to_docs_requests()` makes that unnecessary — it emits batchUpdate requests directly with a `start_index` parameter, so we can target any position in the existing doc without a temp file. One fewer round-trip, one fewer file in Drive's trash, simpler code.

### Atomic-commit plan

Each commit is a green-tests checkpoint. Order:

1. `chore: add PLAN.md for section-edit tools` (this file)
2. `feat(gdocs): add find_heading_range helper`
3. `feat(gdocs): add replace_doc_section_by_heading tool`
4. `feat(gdocs): add append_after_heading tool`
5. `feat(gdocs): add replace_doc_fully_from_markdown tool`
6. `feat(gdrive): add pin_doc_revision tool (Drive revisions API)`
7. `test(gdocs): integration tests against fixtures for the 3 section tools`
8. `test(gdrive): unit test for pin_doc_revision against mocked Drive service`
9. `docs: README section for section-addressable editing`

Each commit:
- passes `pytest` for the existing suite
- includes the `Claude-Session-Id` + `Resume:` trailers per CLAUDE.md
- has a one-line subject + body explaining "why" if non-obvious

## Test plan

### Unit tests (mocked Docs service, in-process)

- `find_heading_range` over fixtures with: no heading match, single match, multiple matches w/ match="first", multiple matches w/ match="exact" (error), nested headings, heading at end of body (section_end = body end), heading inside a tab.
- `replace_doc_section_by_heading` emits exactly one batchUpdate with delete + markdown converter output starting at the right index.
- `append_after_heading` emits insert-only requests at the right index.
- `replace_doc_fully_from_markdown` emits delete + insert at index 1.
- `pin_doc_revision` PATCHes the latest revision with `keepForever=True`.

### Integration test (real Doc, real account — johntrandall@gmail.com)

Create a throwaway test Doc in a `/tmp/jtr-gdocs-section-test` folder. Pre-populate:

```
# Test Doc

## Section A

Paragraph A1. <comment anchored here: "comment-A1">

Paragraph A2.

## Section B

Paragraph B1.

## Section C

Paragraph C1. <suggestion: replace "C1" with "C-one">
```

Run each tool against it. Verify via Drive `revisions.list` and Docs `documents.get`:

| Test | Action | Verify |
|---|---|---|
| T1 | `pin_doc_revision(doc, name="pre-test")` | Latest revision now has `keepForever=true` |
| T2 | `replace_doc_section_by_heading(doc, "Section B", "## Section B\n\nReplaced.")` | Section A's comment still anchored; Section C's suggestion still live; B contents are now "Replaced." |
| T3 | `append_after_heading(doc, "Section A", "Appended paragraph.")` | Comment in A still anchored (not orphaned); new para inserted between A2 and Section B heading |
| T4 | `replace_doc_fully_from_markdown(doc, "# Wiped\n\nNew body.")` | Doc ID unchanged; all comments orphaned; revision history (incl T1 pin) intact |
| T5 | `pin_doc_revision(doc)` after T4 | Second pinned revision; both pins survive |

### Preservation matrix for the README

| Tool | Comments outside range | Suggestions outside range | Comments inside range | Suggestions inside range | Document ID | Named ranges outside |
|---|---|---|---|---|---|---|
| `replace_doc_section_by_heading` | ✅ kept | ✅ kept | ❌ orphaned | ❌ deleted | ✅ same | ✅ kept |
| `append_after_heading` | ✅ kept | ✅ kept | n/a | n/a | ✅ same | ✅ kept |
| `replace_doc_fully_from_markdown` | ❌ all orphaned | ❌ all deleted | ❌ orphaned | ❌ deleted | ✅ same | ❌ all lost |
| `pin_doc_revision` | ✅ no change | ✅ no change | n/a | n/a | ✅ same | ✅ no change |

## Skill integration

The `google-docs-versioning` skill (committed earlier today, ~/.claude/skills/google-docs-versioning/SKILL.md) needs an amendment after this lands:

- Replace references to `create_version` (which is Apps Script in this MCP) with `pin_doc_revision`.
- If the verification phase confirms the Drive API does NOT support naming Doc revisions, soften the "pre-agent-edit-<iso>-<purpose>" naming convention to "use the returned revision ID + timestamp + comment-on-doc pointer" instead.
- Cross-link the 3 section-edit tools as the safe-by-default editing primitives.

## Backwards compatibility

- No existing tools are renamed, removed, or have signatures changed.
- The 4 new tools live in new file regions; no shared mutable state.
- The new `find_heading_range` helper is additive; if upstream merges, internal callers can adopt it gradually.
- `markdown_to_docs_requests` is consumed unchanged.

## Upstream PR readiness

Acceptance criteria for opening the upstream PR:

- All tests pass (`pytest tests/`).
- README updated with section-addressable editing examples + preservation matrix.
- Tool docstrings match the project's existing style (see `get_doc_as_markdown` as canonical reference — gdocs/docs_tools.py:2374).
- No imports of `__future__` or other style drift from project conventions.
- Conventional commit subjects (`feat(gdocs):`, `test(gdocs):`, etc. — matches recent upstream log).
- Integration test results pasted in PR description.

## Risks + open questions (FOR VERIFIERS)

1. **Can the Drive API actually NAME a revision for a native Doc?** The Drive API v3 `revisions` resource documents `keepForever` but the `name` / `publishedRevisionId` fields' behavior for native Docs/Sheets/Slides is murky. Verification target: empirically test PATCH `revisions.update` with a `name`-like field; if no, document the workaround.
2. **Heading-text uniqueness in real docs.** Docs frequently have duplicate heading text ("Notes", "TODO"). `match="first"` is friendly but accident-prone; `match="exact"` is safe but verbose. Is the default right? Should we require explicit choice?
3. **Multi-tab docs.** Should `tab_id` be required for multi-tab docs and forbidden for single-tab, or always optional? Existing tools (`get_doc_as_markdown`) accept it optionally — consistency argument says optional.
4. **Heading level inference.** If a heading text matches at multiple levels (rare but possible: "Summary" as H2 and H3 elsewhere), do we require `heading_level` or pick by occurrence order?
5. **Markdown converter edge cases.** `markdown_to_docs_requests` currently supports CommonMark only — no GFM tables, no strikethrough, no task lists. Does our PR need to enable those or accept the limitation? (Answer: accept the limitation, document it; widening the converter is a separate PR.)
6. **Deployment compatibility.** Our two Portainer stacks (john + frisbee) run `image: v2.1.0`. After merging this branch into our `deployed` branch and rebuilding, will the image start cleanly under the existing compose configs? Are there new pyproject.toml deps to track?
7. **Suggestion-mode interaction.** If a doc is OPEN in someone's browser in Suggesting mode while we batchUpdate, what happens? (Answer: their next save creates a competing revision; our edit wins or theirs does based on revision ordering. Document this; no API fix.)

## Verifier prompts

When the iterative-verification skill runs against this plan, target verifiers should challenge:

- **API verifier:** Confirm every Docs/Drive API call matches the documented surface; spot-check the `start_index` semantics in `markdown_to_docs_requests`; confirm `revisions.update` accepts the body shape we propose.
- **Preservation verifier:** Walk the preservation matrix; identify any edge case where the matrix is wrong (e.g., suggestions that span the boundary).
- **Style verifier:** Read 3 existing tools in `gdocs/docs_tools.py`, confirm our proposed signatures, docstrings, and decorator usage match.
- **Deployment verifier:** Check the build/deploy path — does `localhost:5050/vendor/google-workspace-mcp` image build cleanly from this branch? Are dependencies in `pyproject.toml` already present (`markdown-it-py` etc.)?
