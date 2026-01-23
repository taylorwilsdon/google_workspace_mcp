# Maintenance TODO List

## Recurring Tasks

### Weekly: Upstream Feature Sync (Jules)
**Task:** Sync features from upstream `taylorwilsdon/google_workspace_mcp` repository
**Owner:** Jules (Google's coding agent)
**Frequency:** Weekly
**Documentation:** See `UPSTREAM_SYNC_GUIDE.md` for detailed process

**Quick Reference:**
```bash
# Fetch upstream changes
git fetch upstream

# Review new commits
git log HEAD..upstream/main --oneline

# Create sync branch and integrate changes
git checkout -b upstream-sync-$(date +%Y%m%d)
```

**Key Considerations:**
- Preserve our custom tool consolidations
- Maintain consistent tool schema
- Test all changes thoroughly
- Document what was integrated vs skipped

---

## One-Time Tasks

### Documentation Review
- [ ] Ensure README accurately reflects all Gmail write capabilities
- [ ] Update tool descriptions in manifest.json if needed
- [ ] Verify all docstrings are up to date

### Testing
- [ ] Add integration tests for new Gmail management features
- [ ] Verify all Gmail operations work end-to-end

---

## Completed Tasks

- [x] Implement Gmail trash/untrash/delete operations (2026-01-08)
- [x] Remove overlap between manage_gmail_message and modify_gmail_labels (2026-01-08)
- [x] Create upstream sync guide for Jules (2026-01-08)
