# Weekly Upstream Sync Guide for Jules

This guide provides instructions for Jules (Google's coding agent) to perform weekly syncs of features from the upstream repository while preserving custom tool schemas.

## Overview

**Upstream Repository:** `taylorwilsdon/google_workspace_mcp`
**Fork Repository:** `Coldaine/google_workspace_mcp`
**Frequency:** Weekly
**Objective:** Incorporate new features and improvements from upstream while maintaining custom tool consolidations and schema

## Prerequisites

- Upstream remote configured: `git remote add upstream https://github.com/taylorwilsdon/google_workspace_mcp.git`
- Access to both repositories
- Understanding of the custom tool schema and consolidations

## Weekly Sync Process

### Step 1: Fetch Upstream Changes

```bash
# Fetch latest changes from upstream
git fetch upstream

# Fetch latest from origin
git fetch origin

# Create a new branch for the sync
git checkout -b upstream-sync-$(date +%Y%m%d)
```

### Step 2: Identify New Changes

```bash
# View commits in upstream that we don't have
git log HEAD..upstream/main --oneline --no-merges

# View changed files
git diff HEAD..upstream/main --name-only

# Get detailed diff for specific service directories
git diff HEAD..upstream/main -- gmail/
git diff HEAD..upstream/main -- gdrive/
git diff HEAD..upstream/main -- gcalendar/
git diff HEAD..upstream/main -- gdocs/
git diff HEAD..upstream/main -- gsheets/
git diff HEAD..upstream/main -- gslides/
git diff HEAD..upstream/main -- gforms/
git diff HEAD..upstream/main -- gtasks/
git diff HEAD..upstream/main -- gchat/
git diff HEAD..upstream/main -- gsearch/
```

### Step 3: Analyze Changes for Tool Schema Impact

Review each changed file and categorize changes:

**Categories:**
1. **New Tools** - Brand new functionality not present in our fork
2. **Tool Enhancements** - Improvements to existing tools
3. **Bug Fixes** - Fixes that should be incorporated
4. **Breaking Changes** - Changes that conflict with our schema
5. **Documentation** - Updates to README, docs, etc.

**Critical Files to Review:**
- `gmail/gmail_tools.py` - Gmail tools
- `gdrive/drive_tools.py` - Drive tools
- `gcalendar/calendar_tools.py` - Calendar tools
- `gdocs/docs_tools.py` - Docs tools
- `gsheets/sheets_tools.py` - Sheets tools
- `gslides/slides_tools.py` - Slides tools
- `gforms/forms_tools.py` - Forms tools
- `gtasks/tasks_tools.py` - Tasks tools
- `gchat/chat_tools.py` - Chat tools
- `gsearch/search_tools.py` - Search tools
- `auth/` - Authentication changes
- `core/` - Core server changes

### Step 4: Check Tool Schema Compatibility

**Our Custom Schema Principles:**
1. **Consolidated Tools** - We've reduced tool count through consolidation
2. **Consistent Naming** - Tools follow `action_service_target` pattern
3. **Unified Operations** - Similar operations use consistent parameter patterns
4. **Clear Documentation** - All tools have comprehensive docstrings

**Review Checklist:**
- [ ] Does the upstream change add a new tool that duplicates our consolidated functionality?
- [ ] Does it enhance an existing tool we've consolidated?
- [ ] Does it introduce a new capability we don't have?
- [ ] Is the parameter schema compatible with our patterns?
- [ ] Are there breaking changes to tool signatures?

### Step 5: Selective Merge Strategy

**Option A: Cherry-pick Specific Commits**
```bash
# For individual bug fixes or enhancements
git cherry-pick <commit-hash>

# If conflicts occur, resolve manually
git status
# Edit conflicting files
git add <resolved-files>
git cherry-pick --continue
```

**Option B: Manual Integration**
For complex changes that need adaptation:

1. Review the upstream implementation
2. Identify the core functionality
3. Adapt it to fit our tool schema
4. Implement in our consolidated tool structure
5. Test thoroughly

**Option C: File-level Merge**
```bash
# For non-tool files (auth, core, utils)
git checkout upstream/main -- <file-path>

# Review and test
git diff --cached
```

### Step 6: Testing and Validation

After integrating changes:

```bash
# Run linter
uv run ruff check .

# Run tests
uv run pytest

# Test specific tools manually
uv run python -c "from gmail.gmail_tools import *; print('Gmail tools OK')"
uv run python -c "from gdrive.drive_tools import *; print('Drive tools OK')"
# ... etc for each service
```

### Step 7: Document Changes

Create a sync report in `UPSTREAM_SYNC_REPORT_YYYYMMDD.md`:

```markdown
# Upstream Sync Report - YYYY-MM-DD

## Summary
- Commits reviewed: X
- Changes integrated: Y
- Changes skipped: Z

## Integrated Changes

### New Features
- Feature 1: Description and rationale
- Feature 2: Description and rationale

### Bug Fixes
- Fix 1: Description
- Fix 2: Description

### Enhancements
- Enhancement 1: Description

## Skipped Changes

### Reason: Conflicts with Custom Schema
- Change 1: Why skipped
- Change 2: Why skipped

### Reason: Already Implemented Differently
- Change 1: Our implementation
- Change 2: Our implementation

## Testing Results
- [ ] All linters pass
- [ ] All tests pass
- [ ] Manual testing completed

## Migration Notes
Any special considerations for users or deployment.
```

### Step 8: Commit and Create PR

```bash
# Add all changes
git add -A

# Create comprehensive commit
git commit -m "Weekly upstream sync $(date +%Y-%m-%d)

Integrated upstream changes from taylorwilsdon/google_workspace_mcp:
- [List key changes]

Schema compatibility maintained:
- [List any adaptations made]

Testing:
- All linters pass
- All tests pass

See UPSTREAM_SYNC_REPORT_$(date +%Y%m%d).md for details"

# Push to origin
git push -u origin upstream-sync-$(date +%Y%m%d)
```

### Step 9: Create Pull Request

Use GitHub CLI or web interface:

```bash
gh pr create \
  --title "Weekly Upstream Sync $(date +%Y-%m-%d)" \
  --body "$(cat UPSTREAM_SYNC_REPORT_$(date +%Y%m%d).md)" \
  --base main
```

## Special Considerations

### Tool Consolidations to Preserve

Our fork has consolidated tools for efficiency. When syncing upstream:

**Gmail Tools:**
- Upstream may have separate tools for read/send/draft/labels
- We've consolidated into fewer, more powerful tools
- Preserve our `manage_gmail_message`, `get_gmail_content`, `modify_gmail_labels` structure

**Docs Tools:**
- Upstream may have 14+ separate tools
- We've consolidated to 7 core tools
- Preserve our consolidation pattern

**Other Services:**
- Follow similar patterns for other Google services
- Consolidate where it makes sense
- Maintain consistent API patterns

### Breaking Changes Policy

If upstream introduces breaking changes:

1. **Assess Impact:** Determine if it affects our consolidated schema
2. **Create Migration Path:** Plan how to update our tools
3. **Version Appropriately:** Consider major version bump if needed
4. **Document Thoroughly:** Update README and migration guides
5. **Notify Users:** If this is a public fork, announce breaking changes

### Automation Opportunities

Consider setting up:

1. **GitHub Action** - Weekly automated check for upstream changes
2. **Notification System** - Alert when new upstream commits detected
3. **Automated Diff Reports** - Generate preliminary analysis
4. **Test Automation** - Automatically run tests on sync branches

## Quick Reference Commands

```bash
# Setup (one-time)
git remote add upstream https://github.com/taylorwilsdon/google_workspace_mcp.git

# Weekly sync start
git fetch upstream && git fetch origin
git checkout -b upstream-sync-$(date +%Y%m%d)

# View changes
git log HEAD..upstream/main --oneline
git diff HEAD..upstream/main --stat

# Test integration
uv run ruff check .
uv run pytest

# Create PR
git push -u origin upstream-sync-$(date +%Y%m%d)
gh pr create --title "Weekly Upstream Sync $(date +%Y-%m-%d)" --base main
```

## Troubleshooting

### Merge Conflicts

If you encounter merge conflicts:

1. **Understand Both Sides:** Review our version and upstream version
2. **Prefer Our Schema:** When in doubt, preserve our consolidated tool schema
3. **Extract Functionality:** Take the new functionality, adapt to our patterns
4. **Test Thoroughly:** Ensure resolution doesn't break existing functionality

### Test Failures

If tests fail after integration:

1. **Identify Source:** Determine if failure is from upstream change or integration
2. **Fix or Revert:** Either fix the issue or revert the problematic change
3. **Document:** Note why a change was reverted in the sync report

### Schema Incompatibilities

If upstream changes conflict fundamentally with our schema:

1. **Document the Conflict:** Explain in sync report
2. **Consider Alternatives:** Can we implement the feature differently?
3. **Evaluate Trade-offs:** Is upstream approach better? Should we adapt?
4. **Make Informed Decision:** Choose based on our fork's goals

## Resources

- **Upstream Repository:** https://github.com/taylorwilsdon/google_workspace_mcp
- **Our Fork:** https://github.com/Coldaine/google_workspace_mcp
- **Tool Consolidation Plan:** See `CONSOLIDATION_PLAN.md`
- **Testing Strategy:** See `TESTING_PLAN.md`

## Contact

If you encounter issues or need clarification:
- Check existing documentation
- Review previous sync reports
- Consult with repository maintainer

---

**Last Updated:** $(date +%Y-%m-%d)
**Next Scheduled Sync:** [Set weekly schedule]
