# Security Policy

## Reporting Security Issues

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, please email us at **taylor@workspacemcp.com**

Please include as much of the following information as you can to help us better understand and resolve the issue:

- The type of issue (e.g., authentication bypass, credential exposure, command injection, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit the issue

This information will help us triage your report more quickly.

## Supported Versions

We release patches for security vulnerabilities. Which versions are eligible for receiving such patches depends on the CVSS v3.0 Rating:

| Version | Supported          |
| ------- | ------------------ |
| 1.4.x   | :white_check_mark: |
| < 1.4   | :x:                |

## Security Considerations

When using this MCP server, please ensure:

1. Store Google OAuth credentials securely
2. Never commit credentials to version control
3. Use environment variables for sensitive configuration
4. Regularly rotate OAuth refresh tokens
5. Limit OAuth scopes to only what's necessary
6. Keep local file reads narrowly scoped. By default, path-based attachments are limited to `WORKSPACE_ATTACHMENT_DIR`; expanding `ALLOWED_FILE_DIRS` increases exposure to prompt-injection-driven exfiltration.

### Workspace administration safeguards

The admin services are opt-in and are absent from the default launcher. Each admin call must use credentials verified as the selected account, recheck that the actor is an active admin in the target's customer, and enforce the selected service's permission level at execution time. Destructive offboarding actions also reject self-suspension or self-deletion and removal of the final active super admin. Never treat a caller-supplied email or customer ID as an authorization claim. Data Transfer must complete before licenses or the user account can be deleted. The per-step one-use confirmation is a server check, not an MCP client prompt.

A workflow needs private durable state for its workflow IDs, step states, transfer ID, and short-lived confirmation token hashes. A multi-node or stateless deployment without shared atomic storage and locks cannot safely resume one. An uncertain write must be reconciled against Google, or stopped for manual investigation, rather than blindly repeated. Offboarding does not check Vault retention, all app-specific data, or all delegated access, and the registered API-step status is not a blanket data-retention or compliance claim. See [the current capability and gap report](docs/admin-capability-coverage.md). Do not enable admin scopes or test a live write without the intended organization's verified admin identity, customer ID, entitlements, and separate authorization.

For more information on securing your use of the project, see https://workspacemcp.com/privacy

## Preferred Languages

We prefer all communications to be in English.

## Policy

We follow the principle of responsible disclosure. We will make every effort to address security issues in a timely manner and will coordinate with reporters to understand and resolve issues before public disclosure.
