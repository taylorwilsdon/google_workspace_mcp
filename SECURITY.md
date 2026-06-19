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

For more information on securing your use of the project, see https://workspacemcp.com/privacy

## Preferred Languages

We prefer all communications to be in English.

## Policy

We follow the principle of responsible disclosure. We will make every effort to address security issues in a timely manner and will coordinate with reporters to understand and resolve issues before public disclosure.
