# Google Workspace MCP Server Helm Chart

This Helm chart deploys the Google Workspace MCP Server on a Kubernetes cluster. The Google Workspace MCP Server provides comprehensive integration with Google Workspace services including Gmail, Calendar, Drive, Docs, Sheets, Slides, Forms, Tasks, and Chat.

Disclaimer - this is a user submitted feature and not one that the maintainer uses personally. It may be out of date - use at your own risk! 

## Prerequisites

- Kubernetes 1.19+
- Helm 3.2.0+
- Google Cloud Project with OAuth 2.0 credentials
- Enabled Google Workspace APIs

## Installing the Chart

To install the chart with the release name `workspace-mcp`:

```bash
# First, set your Google OAuth credentials
helm install workspace-mcp ./helm-chart/workspace-mcp \
  --set secrets.googleOAuth.clientId="your-client-id.apps.googleusercontent.com" \
  --set secrets.googleOAuth.clientSecret="your-client-secret"
```

## Configuration

The following table lists the configurable parameters and their default values:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Container image repository | `workspace-mcp` |
| `image.tag` | Container image tag | `""` (uses Chart.AppVersion) |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `secrets.googleOAuth.clientId` | Google OAuth Client ID | `""` (required) |
| `secrets.googleOAuth.clientSecret` | Google OAuth Client Secret | `""` (required) |
| `secrets.googleOAuth.userEmail` | Default user email for single-user mode | `""` |
| `singleUserMode` | Enable single-user mode | `false` |
| `tools.enabled` | List of tools to enable | `[]` (all tools enabled) |
| `env.MCP_ENABLE_OAUTH21` | Enable OAuth 2.1 support for streamable HTTP | `"true"` |
| `service.type` | Kubernetes service type | `ClusterIP` |
| `service.port` | Service port | `8000` |
| `ingress.enabled` | Enable ingress | `false` |
| `resources.limits.cpu` | CPU limit | `500m` |
| `resources.limits.memory` | Memory limit | `512Mi` |
| `autoscaling.enabled` | Enable HPA | `false` |

## Google OAuth Setup

Before deploying, you need to set up Google OAuth credentials:

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the required Google Workspace APIs
3. Create OAuth 2.0 credentials (Web application)
4. Set authorized redirect URI: `http://your-domain:8000/oauth2callback`

## Examples

### Basic deployment with specific tools:

```bash
helm install workspace-mcp ./helm-chart/workspace-mcp \
  --set secrets.googleOAuth.clientId="your-client-id" \
  --set secrets.googleOAuth.clientSecret="your-secret" \
  --set tools.enabled="{gmail,calendar,drive}"
```

### Production deployment with ingress:

```bash
helm install workspace-mcp ./helm-chart/workspace-mcp \
  --set secrets.googleOAuth.clientId="your-client-id" \
  --set secrets.googleOAuth.clientSecret="your-secret" \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host="workspace-mcp.yourdomain.com" \
  --set ingress.hosts[0].paths[0].path="/" \
  --set ingress.hosts[0].paths[0].pathType="Prefix"
```

### Single-user mode deployment:

Single-user mode is a legacy local-auth mode and is incompatible with OAuth 2.1.
For Kubernetes, prefer the default OAuth 2.1 mode unless this deployment is
strictly limited to trusted network paths.

```bash
helm install workspace-mcp ./helm-chart/workspace-mcp \
  --set secrets.googleOAuth.clientId="your-client-id" \
  --set secrets.googleOAuth.clientSecret="your-secret" \
  --set env.MCP_ENABLE_OAUTH21="false" \
  --set singleUserMode=true \
  --set secrets.googleOAuth.userEmail="user@yourdomain.com"
```

### OAuth 2.1 multi-user deployment:

```bash
helm install workspace-mcp ./helm-chart/workspace-mcp \
  --set secrets.googleOAuth.clientId="your-client-id" \
  --set secrets.googleOAuth.clientSecret="your-secret"
```

## Uninstalling the Chart

To uninstall/delete the `workspace-mcp` deployment:

```bash
helm delete workspace-mcp
```

## Available Tools

You can selectively enable tools using the `tools.enabled` parameter:

- `gmail` - Gmail integration
- `drive` - Google Drive integration  
- `calendar` - Google Calendar integration
- `docs` - Google Docs integration
- `sheets` - Google Sheets integration
- `slides` - Google Slides integration
- `forms` - Google Forms integration
- `tasks` - Google Tasks integration
- `chat` - Google Chat integration
- `search` - Google Custom Search integration

If `tools.enabled` is empty or not set, all tools will be enabled.

## Health Checks

The chart includes health checks that verify the application is running correctly:

- Liveness probe checks `/health` endpoint
- Readiness probe ensures the service is ready to accept traffic
- Configurable timing and thresholds via `healthCheck` values

## Security

- Runs as non-root user (UID 1000)
- Uses read-only root filesystem where possible
- Drops all Linux capabilities
- Secrets are stored securely in Kubernetes secrets

## Support

For issues and questions:
- GitHub: https://github.com/taylorwilsdon/google_workspace_mcp
- Documentation: https://workspacemcp.com
