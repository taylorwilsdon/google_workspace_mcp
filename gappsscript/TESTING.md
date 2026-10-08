# Apps Script MCP Testing Guide

This document provides instructions for running unit tests and end-to-end (E2E) tests for the Apps Script MCP feature.

## Test Structure

```
tests/gappsscript/
    __init__.py
    test_apps_script_tools.py   # Unit tests with mocked API
    manual_test.py              # E2E tests against real API
```

## Unit Tests

Unit tests use mocked API responses and do not require Google credentials.

### Running Unit Tests

```bash
# Run all Apps Script unit tests
uv run pytest tests/gappsscript/test_apps_script_tools.py -v

# Run specific test
uv run pytest tests/gappsscript/test_apps_script_tools.py::test_list_script_projects -v

# Run with coverage
uv run pytest tests/gappsscript/test_apps_script_tools.py --cov=gappsscript
```

### Test Coverage

Unit tests cover the `_impl` functions behind each tool plus the action-dispatch
wrappers:
- get_script_project: list (Drive API), get project, get file
- manage_script_project: create, delete
- manage_script_content: update
- run_script_function
- list_script_deployments
- manage_deployment: create, update, delete
- get_script_version: list, get
- manage_script_version: create
- get_script_activity: processes, metrics
- manage_script_trigger: list, delete

## E2E Tests

E2E tests interact with the real Google Apps Script API. They require valid OAuth credentials and will create real resources in your Google account.

### Prerequisites

1. **Google Cloud Project** with Apps Script API and Drive API enabled
2. **OAuth credentials** (Desktop application type)
3. **Test user** added to OAuth consent screen

### Setup

**Option 1: Default paths (recommended for CI)**

Place credentials in the project root:
```bash
# Place your OAuth client credentials here
cp /path/to/your/client_secret.json ./client_secret.json
```

**Option 2: Custom paths via environment variables**

```bash
export GOOGLE_CLIENT_SECRET_PATH=/path/to/client_secret.json
export GOOGLE_TOKEN_PATH=/path/to/token.pickle
```

### Running E2E Tests

```bash
# Interactive mode (prompts for confirmation)
uv run python tests/gappsscript/manual_test.py

# Non-interactive mode (for CI)
uv run python tests/gappsscript/manual_test.py --yes
```

### E2E Test Flow

The test script performs the following operations:

1. **List Projects** - Lists existing Apps Script projects via Drive API
2. **Create Project** - Creates a new test project
3. **Get Project** - Retrieves project details
4. **Update Content** - Adds code to the project
5. **Run Function** - Attempts to execute a function (see note below)
6. **Create Deployment** - Creates a versioned deployment
7. **List Deployments** - Lists all deployments
8. **List Processes** - Lists recent script executions

### Cleanup

The test script does not automatically delete created projects. After running tests:

1. Go to [Google Apps Script](https://script.google.com/)
2. Find projects named "MCP Test Project"
3. Delete them manually via the menu (three dots) > Remove

## Headless Linux Testing

For headless environments (servers, CI/CD, WSL without GUI):

### OAuth Authentication Flow

The test script uses a headless-compatible OAuth flow:

1. Script prints an authorization URL
2. Open the URL in any browser (can be on a different machine)
3. Complete Google sign-in and authorization
4. Browser redirects to `http://localhost/?code=...` (page will not load)
5. Copy the full URL from the browser address bar
6. Paste it into the terminal when prompted

### Example Session

```
$ python tests/gappsscript/manual_test.py --yes

============================================================
HEADLESS AUTH
============================================================

1. Open this URL in any browser:

https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=...

2. Sign in and authorize the app
3. You'll be redirected to http://localhost (won't load)
4. Copy the FULL URL from browser address bar
   (looks like: http://localhost/?code=4/0A...&scope=...)
5. Paste it below:

Paste full redirect URL: http://localhost/?code=4/0AQSTgQ...&scope=...

Building API services...

=== Test: List Projects ===
Found 3 Apps Script projects:
...
```

### Credential Storage

OAuth tokens are stored as pickle files:
- Default: `./test_token.pickle` in project root
- Custom: Set via `GOOGLE_TOKEN_PATH` environment variable

Tokens are reused on subsequent runs until they expire or are revoked.

## Known Limitations and Caveats

### run_script_function Test Failure

The "Run Function" test will fail with a 404 error unless you manually configure the script as an API Executable. This is a Google platform requirement, not a bug.

To make run_script_function work:

1. Open the created test script in Apps Script editor
2. Go to Project Settings > Change GCP project
3. Enter your GCP project number
4. Deploy as "API Executable"

For E2E testing purposes, it is acceptable for this test to fail. All other tests should pass.

### Drive API Requirement

The `get_script_project` list action uses the Google Drive API (not the Apps Script API) because the Apps Script API does not provide a projects.list endpoint. Ensure the Drive API is enabled in your GCP project.

### Scope Requirements

The E2E tests require these scopes:
- `script.projects` and `script.projects.readonly`
- `script.deployments` and `script.deployments.readonly`
- `script.processes`
- `drive.readonly`

If you encounter "insufficient scopes" errors, delete the stored token file and re-authenticate.

### Rate Limits

Google enforces rate limits on the Apps Script API. If running tests repeatedly, you may encounter quota errors. Wait a few minutes before retrying.

## CI/CD Integration

For automated testing in CI/CD pipelines:

### Unit Tests Only (Recommended)

```yaml
# GitHub Actions example
- name: Run unit tests
  run: uv run pytest tests/gappsscript/test_apps_script_tools.py -v
```

### E2E Tests in CI

E2E tests require OAuth credentials. Options:

1. **Skip E2E in CI** - Run only unit tests in CI, run E2E locally
2. **Service Account** - Not supported (Apps Script API requires user OAuth)
3. **Pre-authenticated Token** - Store encrypted token in CI secrets

To use a pre-authenticated token:
```bash
# Generate token locally
python tests/gappsscript/manual_test.py

# Store test_token.pickle contents as base64 in CI secret
base64 test_token.pickle > token.b64

# In CI, restore and set path
echo $TOKEN_SECRET | base64 -d > test_token.pickle
export GOOGLE_TOKEN_PATH=./test_token.pickle
python tests/gappsscript/manual_test.py --yes
```

Note: Tokens expire and must be refreshed periodically.

## Troubleshooting

### "Apps Script API has not been used in project"

Enable the Apps Script API in your GCP project:
https://console.cloud.google.com/flows/enableapi?apiid=script.googleapis.com

### "Access Not Configured. Drive API has not been used"

Enable the Drive API in your GCP project:
https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com

### "Request had insufficient authentication scopes"

Delete the token file and re-authenticate:
```bash
rm test_token.pickle
python tests/gappsscript/manual_test.py
```

### "User is not authorized to access this resource"

Ensure your email is added as a test user in the OAuth consent screen configuration.

### "Requested entity was not found" (404 on run)

The script needs to be deployed as "API Executable". See the run_script_function section above.

### OAuth redirect fails on headless machine

The redirect to `http://localhost` is expected to fail. Copy the URL from the browser address bar (including the error page URL) and paste it into the terminal.
