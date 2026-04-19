AI OS — multi-root workspace (local)

Multi-repo routing (where to implement what):
  See AI_OS_MULTI_REPO_ROUTING.md in this folder.

Open in Cursor/VS Code:
  File → Open Workspace from File… → AI_OS.code-workspace

Paths are relative to this folder (C:\Apps\AI_OS_Workspace):
  - aios-governing-docs  (canonical clone; .env / .env.local gitignored)
  - ai-os-voice-interface
  - google-workspace-mcp  (repo: aios_google_workspace_mcp)

Update %USERPROFILE%\.cursor\mcp.json if the governance MCP server should point at the
canonical governing-docs scripts path instead of C:\Apps\governing_docs\...

Do not commit secrets; keep API keys in ignored .env files per repo.
