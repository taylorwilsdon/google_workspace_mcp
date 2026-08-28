# Contributing to Google Workspace MCP

Thanks for your interest in contributing! This guide covers what you need to know to get a pull request merged.

## Before You Start

**Extending existing tools is strongly preferred over adding new ones.** We are near the maximum tool count allowed by the OpenAI-compatible native tools payload, so PRs that add entirely new tools will almost never be merged. Instead, look for ways to extend or improve the tools that already exist.

## Development Setup

```bash
# Install with dev + test extras
uv sync --extra test

# Run the linter and formatter
uvx ruff check
uvx ruff format

# Run tests
uv run pytest
```

## CI Requirements

Every PR must pass these automated checks before review:

- **Ruff** - linting and formatting (auto-fixed for same-repo PRs, but forks must pass on their own)
- **Pytest** - the full test suite must pass
- **Maintainer edits enabled** - fork PRs must have "Allow edits from maintainers" checked

Run `uvx ruff check && uvx ruff format --check && uv run pytest` locally before pushing. If CI fails, your PR will not be reviewed.

## Code Organization

The codebase separates each Google service into its own package with a strict split between tool definitions and business logic:

```
gmail/
    gmail_tools.py    # Tool definitions only
    gmail_helpers.py  # All business logic, API calls, data transforms
```

**Keep tool files thin.** A `*_tools.py` file should contain tool function signatures, docstrings, and a call into the corresponding `*_helpers.py`. Helper functions, API interaction logic, response formatting, and anything reusable belongs in the helpers module. PRs that drop utility functions into tool files will be asked to refactor before merge. This is the single most common reason PRs need revision.

## Pull Request Guidelines

- Keep changes focused. One bug fix or one improvement per PR.
- Include tests for new behavior. Look at the existing `tests/` directory for patterns.
- Follow PEP 8. Imports go at the top of the file in the standard order (stdlib, third-party, local), never nested inside functions or classes.
- Don't introduce new dependencies without discussion in an issue first.
- Enable "Allow edits from maintainers" on your PR (required by CI for forks).

## What Gets Merged

- Bug fixes with a test proving the fix
- Performance improvements to existing tools
- Better error messages and edge-case handling
- Documentation improvements
- Test coverage improvements

## What Probably Won't Get Merged

- New tools (we're at the payload size limit)
- Large refactors without prior discussion
- Changes that don't pass CI
- PRs with helper/utility logic inlined into tool files

## Reporting Issues

Use the [bug report](https://github.com/taylorwilsdon/google_workspace_mcp/issues/new?template=bug-report.md) or [feature request](https://github.com/taylorwilsdon/google_workspace_mcp/issues/new?template=feature_request.md) templates when opening an issue.
