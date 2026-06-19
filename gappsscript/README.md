# Google Apps Script MCP Tools

This module provides Model Context Protocol (MCP) tools for interacting with Google Apps Script API, enabling AI agents to create, manage, and execute Apps Script projects programmatically.

## Overview

Google Apps Script allows automation and extension of Google Workspace applications. This MCP integration provides 17 tools across core and extended tiers for complete Apps Script lifecycle management.

## Why Apps Script?

Apps Script is the automation glue of Google Workspace. While individual service APIs (Docs, Sheets, Gmail) operate on single resources, Apps Script enables:

- **Cross-app automation** - Orchestrate workflows across Sheets, Gmail, Calendar, Forms, and Drive
- **Persistent logic** - Host custom business rules inside Google's environment
- **Scheduled execution** - Run automations on time-based or event-driven triggers
- **Advanced integration** - Access functionality not available through standard APIs

This MCP integration allows AI agents to author, debug, deploy, and operate these automations end-to-end - something not possible with individual Workspace APIs alone.

### What This Enables

| Without Apps Script MCP | With Apps Script MCP |
|------------------------|---------------------|
| Read/update Sheets, Docs, Gmail individually | Create long-lived automations across services |
| No persistent automation logic | Host business logic that executes repeatedly |
| Manual workflow orchestration | Automated multi-step workflows |
| No execution history | Debug via execution logs and status |
| No deployment versioning | Manage deployments and roll back versions |

### Complete Workflow Example

**Scenario:** Automated weekly report system

```
User: "Create a script that runs every Monday at 9 AM. It should:
1. Read data from the 'Sales' spreadsheet
2. Calculate weekly totals and growth percentages
3. Generate a summary with the top 5 performers
4. Email the report to team@company.com
5. Log any errors to a monitoring sheet"
```

The AI agent:
1. Creates a new Apps Script project
2. Generates the complete automation code
3. Deploys the script
4. Sets up the time-based trigger
5. Tests execution and monitors results

All through natural language - no JavaScript knowledge required.

### AI Agent Workflow Pattern

The MCP client typically follows this pattern when working with Apps Script:

1. **Inspect** - Read existing script code and project structure
2. **Analyze** - Understand current functionality and identify issues
3. **Propose** - Generate code changes or new functionality
4. **Update** - Modify files atomically with complete version control
5. **Execute** - Run functions to test changes
6. **Deploy** - Create versioned deployments for production use
7. **Monitor** - Check execution logs and debug failures

This ensures safe, auditable automation management.

## Features

### Project Management
- List all Apps Script projects
- Get complete project details including all files
- Create new standalone or bound script projects
- Update script content (add/modify JavaScript files)
- Delete script projects

### Execution
- Execute functions with parameters
- Development mode for testing latest code
- Production deployment execution
- View execution history and status

### Deployment Management
- Create new deployments
- List all deployments for a project
- Update deployment configurations
- Delete outdated deployments

### Version Management
- List all versions of a script
- Create immutable version snapshots
- Get details of specific versions

### Monitoring & Analytics
- View recent script executions
- Check execution status and results
- Monitor for errors and failures
- Get execution metrics (active users, total executions, failures)

### Trigger Code Generation
- Generate Apps Script code for time-based triggers (minutes, hours, daily, weekly)
- Generate code for event triggers (onOpen, onEdit, onFormSubmit, onChange)
- Provides ready-to-use code snippets with setup instructions

## Limitations & Non-Goals

**Current Limitations**
- Direct trigger management via API is not supported (use `generate_trigger_code` instead)
- Real-time debugging and breakpoints are not available
- Advanced service enablement must be done manually in the script editor

**Non-Goals**
- This integration does not replace the Apps Script editor UI
- Does not execute arbitrary JavaScript outside defined script functions
- Does not provide IDE features like autocomplete or syntax highlighting

**Workarounds**
- Triggers: Use `generate_trigger_code` to get ready-to-use Apps Script code for any trigger type
- Advanced services can be enabled via the manifest file (appsscript.json)
- Debugging is supported through execution logs, metrics, and error monitoring

## Prerequisites

### 1. Google Cloud Project Setup

Before using the Apps Script MCP tools, configure your Google Cloud project:

**Step 1: Enable Required APIs**

Enable these APIs in your Google Cloud Console:

1. [Apps Script API](https://console.cloud.google.com/flows/enableapi?apiid=script.googleapis.com) (required for all operations)
2. [Google Drive API](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com) (required for listing projects)

**Step 2: Create OAuth Credentials**

1. Go to [APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials)
2. Click "Create Credentials" > "OAuth client ID"
3. Select "Desktop application" as the application type
4. Download the JSON file and save as `client_secret.json`

**Step 3: Configure OAuth Consent Screen**

1. Go to [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
2. Add yourself as a test user (required for unverified apps)
3. Add the required scopes (see below)

### 2. OAuth Scopes

The following OAuth scopes are required:

```
https://www.googleapis.com/auth/script.projects
https://www.googleapis.com/auth/script.projects.readonly
https://www.googleapis.com/auth/script.deployments
https://www.googleapis.com/auth/script.deployments.readonly
https://www.googleapis.com/auth/script.processes
https://www.googleapis.com/auth/script.metrics
https://www.googleapis.com/auth/script.external_request
https://www.googleapis.com/auth/script.scriptapp
https://www.googleapis.com/auth/drive.file
```

These are automatically requested when using the appscript tool tier.

### 3. Running the MCP Server

Start the server with Apps Script tools enabled:

```bash
uv run main.py --tools appscript --single-user
```

Or include with other tools:

```bash
uv run main.py --tools appscript drive sheets
```

On first use, you will be prompted to authorize the application. Complete the OAuth flow in your browser.

## Tool Tiers

### Core Tier
Essential operations for reading, writing, and executing scripts:

- `list_script_projects`: List accessible projects
- `get_script_project`: Get full project with all files
- `get_script_content`: Get specific file content
- `create_script_project`: Create new project
- `update_script_content`: Modify project files
- `run_script_function`: Execute functions
- `generate_trigger_code`: Generate trigger setup code

### Extended Tier
Advanced deployment, versioning, and monitoring:

- `create_deployment`: Create new deployment
- `list_deployments`: List all deployments
- `update_deployment`: Update deployment config
- `delete_deployment`: Remove deployment
- `delete_script_project`: Delete a project permanently
- `list_versions`: List all versions
- `create_version`: Create immutable version snapshot
- `get_version`: Get version details
- `list_script_processes`: View execution history
- `get_script_metrics`: Get execution analytics

## Usage Examples

### List Projects

```python
# List all Apps Script projects
uv run main.py --tools appscript
# In MCP client: "Show me my Apps Script projects"
```

Example output:
```
Found 3 Apps Script projects:
- Email Automation (ID: abc123) Created: 2025-01-10 Modified: 2026-01-12
- Sheet Processor (ID: def456) Created: 2025-06-15 Modified: 2025-12-20
- Form Handler (ID: ghi789) Created: 2024-11-03 Modified: 2025-08-14
```

### Create New Project

```python
# Create a new Apps Script project
# In MCP client: "Create a new Apps Script project called 'Data Sync'"
```

Example output:
```
Created Apps Script project: Data Sync
Script ID: new123
Edit URL: https://script.google.com/d/new123/edit
```

### Get Project Details

```python
# Get complete project with all files
# In MCP client: "Show me the code for script abc123"
```

Example output:
```
Project: Email Automation (ID: abc123)
Creator: user@example.com
Created: 2025-01-10
Modified: 2026-01-12

Files:
1. Code.gs (SERVER_JS)
   function sendDailyEmail() {
     var sheet = SpreadsheetApp.getActiveSpreadsheet();
     // ... email logic
   }

2. appsscript.json (JSON)
   {"timeZone": "America/New_York", "dependencies": {}}
```

### Update Script Content

```python
# Update script files
# In MCP client: "Update my email script to add error handling"
```

The AI will:
1. Read current code
2. Generate improved version
3. Call `update_script_content` with new files

### Run Script Function

```python
# Execute a function
# In MCP client: "Run the sendDailyEmail function in script abc123"
```

Example output:
```
Execution successful
Function: sendDailyEmail
Result: Emails sent to 5 recipients
```

### Create Deployment

```python
# Deploy script for production
# In MCP client: "Deploy my email automation to production"
```

Example output:
```
Created deployment for script: abc123
Deployment ID: AKfy...xyz
Description: Production release
```

## Common Workflows

### 1. Create Automated Workflow (Complete Example)

**Scenario:** Form submission handler that sends customized emails

```
User: "When someone submits the Contact Form:
1. Get their email and department from the form response
2. Look up their manager in the Team Directory spreadsheet
3. Send a welcome email to the submitter
4. Send a notification to their manager
5. Log the interaction in the Onboarding Tracker sheet"
```

**AI Agent Steps:**
```
1. "Create a new Apps Script bound to the Contact Form"
2. "Add a function that reads form submissions"
3. "Connect to the Team Directory spreadsheet to look up managers"
4. "Generate personalized email templates for both messages"
5. "Add logging to the Onboarding Tracker"
6. "Run the function to test it with sample data"
7. "Create a production deployment"
```

Result: Complete automation created and deployed without writing code.

### 2. Debug Existing Script

```
User: "My expense tracker script is failing"
AI: "Show me the code for the expense tracker script"
AI: "What errors occurred in recent executions?"
AI: "The calculateTotal function has a division by zero error on line 23"
AI: "Fix the error by adding a check for zero values"
AI: "Run calculateTotal to verify the fix"
User: "Create a new deployment with the bug fix"
```

### 3. Modify and Extend Automation

```
User: "Update my weekly report script to include sales data from the Q1 sheet"
AI: "Read the current report generation script"
AI: "Add Q1 data fetching to the generateReport function"
AI: "Test the updated function"
User: "Looks good, deploy it"
AI: "Create a new deployment with description 'Added Q1 sales data'"
```

### 4. Run Existing Business Logic

```
User: "Run the monthlyCleanup function in my Data Management script"
User: "What does the calculateCommission function do?"
User: "Execute reconcileAccounts with parameters: ['2024', 'January']"
```

## File Types

Apps Script projects support three file types:

- **SERVER_JS**: Google Apps Script code (.gs files)
- **HTML**: HTML files for custom UIs
- **JSON**: Manifest file (appsscript.json)

## API Limitations

### Execution Timeouts
- Simple triggers: 30 seconds
- Custom functions: 30 seconds
- Script execution via API: 6 minutes

### Quota Limits
- Script executions per day: varies by account type
- URL Fetch calls: 20,000 per day (consumer accounts)

See [Apps Script Quotas](https://developers.google.com/apps-script/guides/services/quotas) for details.

### Cannot Execute Arbitrary Code
The `run_script_function` tool can only execute functions that are defined in the script. You cannot run arbitrary JavaScript code directly. To run new code:

1. Add function to script via `update_script_content`
2. Execute the function via `run_script_function`
3. Optionally remove the function after execution

### run_script_function Requires API Executable Deployment

The `run_script_function` tool requires additional manual configuration in the Apps Script editor:

**Why this limitation exists:**
Google requires scripts to be explicitly deployed as "API Executable" before they can be invoked via the Apps Script API. This is a security measure to prevent unauthorized code execution.

**To enable API execution:**

1. Open the script in the Apps Script editor
2. Go to Project Settings (gear icon)
3. Under "Google Cloud Platform (GCP) Project", click "Change project"
4. Enter your GCP project number (found in Cloud Console dashboard)
5. Click "Deploy" > "New deployment"
6. Select type: "API Executable"
7. Set "Who has access" to "Anyone" or "Anyone with Google account"
8. Click "Deploy"

After completing these steps, the `run_script_function` tool will work for that script.

**Note:** All other tools (create, update, list, deploy) work without this manual step. Only function execution via API requires the API Executable deployment.

## Error Handling

Common errors and solutions:

### 404: Script not found
- Verify script ID is correct
- Ensure you have access to the project

### 403: Permission denied
- Check OAuth scopes are authorized
- Verify you own or have access to the project

### Execution timeout
- Script exceeded 6-minute limit
- Optimize code or split into smaller functions

### Script authorization required
- Function needs additional permissions
- User must manually authorize in script editor

## Security Considerations

### OAuth Scopes
Scripts inherit the OAuth scopes of the MCP server. Functions that access other Google services (Gmail, Drive, etc.) will only work if those scopes are authorized.

### Script Permissions
Scripts run with the permissions of the script owner, not the user executing them. Be cautious when:
- Running scripts you did not create
- Granting additional permissions to scripts
- Executing functions that modify data

### Code Review
Always review code before executing, especially for:
- Scripts from unknown sources
- Functions that access sensitive data
- Operations that modify or delete data

## Testing

### Unit Tests
Run unit tests with mocked API responses:

```bash
uv run pytest tests/gappsscript/test_apps_script_tools.py
```

### Manual Testing
Test against real Apps Script API:

```bash
python tests/gappsscript/manual_test.py
```

Note: Manual tests create real projects in your account. Delete test projects after running.

## References

### Apps Script Documentation
- [Apps Script Overview](https://developers.google.com/apps-script/overview) - Introduction and capabilities
- [Apps Script Guides](https://developers.google.com/apps-script/guides/services) - Service-specific guides
- [Apps Script Reference](https://developers.google.com/apps-script/reference) - Complete API reference

### Apps Script API (for this MCP integration)
- [Apps Script API Overview](https://developers.google.com/apps-script/api) - API features and concepts
- [REST API Reference](https://developers.google.com/apps-script/api/reference/rest) - Endpoint documentation
- [OAuth Scopes](https://developers.google.com/apps-script/api/how-tos/authorization) - Required permissions

### Useful Resources
- [Apps Script Quotas](https://developers.google.com/apps-script/guides/services/quotas) - Usage limits and restrictions
- [Best Practices](https://developers.google.com/apps-script/guides/support/best-practices) - Performance and optimization
- [Troubleshooting](https://developers.google.com/apps-script/guides/support/troubleshooting) - Common issues and solutions

## Troubleshooting

### "Apps Script API has not been used in project"
Enable the API in Google Cloud Console

### "Insufficient Permission"
- Verify OAuth scopes are authorized
- Re-authenticate if needed

### "Function not found"
- Check function name spelling
- Verify function exists in the script
- Ensure function is not private

### "Invalid project structure"
- Ensure at least one .gs file exists
- Verify JSON files are valid JSON
- Check file names don't contain invalid characters

## Contributing

When adding new Apps Script tools:

1. Follow existing patterns in `apps_script_tools.py`
2. Add comprehensive docstrings
3. Include unit tests
4. Update this README with examples
5. Test against real API before submitting

## License

MIT License - see project root LICENSE file
