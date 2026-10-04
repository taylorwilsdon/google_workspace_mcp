import { Container } from "@cloudflare/containers";
import { pickContainerInstance } from "./pool";

export interface Env {
	WORKSPACE_MCP_CONTAINER: DurableObjectNamespace<WorkspaceMcpContainer>;
	GOOGLE_OAUTH_CLIENT_ID: string;
	GOOGLE_OAUTH_CLIENT_SECRET: string;
}

/**
 * One stateless copy of the Google Workspace MCP image.
 *
 * User tokens are not stored here. The orgs Agent DO puts the member's Google
 * access token on the request, and the process validates it and drops it.
 */
export class WorkspaceMcpContainer extends Container<Env> {
	defaultPort = 8000;
	sleepAfter = "10m";

	override onStart(): void {
		console.log("workspace-mcp container listening");
	}

	override onStop(): void {
		console.log("workspace-mcp container stopped");
	}

	override onError(error: unknown): void {
		console.error("workspace-mcp container error", error instanceof Error ? error.name : "unknown");
	}

	constructor(ctx: DurableObjectState, env: Env) {
		// @cloudflare/containers types this argument as DurableObject["ctx"],
		// which the installed workers-types package does not declare.
		super(ctx as ConstructorParameters<typeof Container>[0], env);
		this.envVars = {
			PORT: "8000",
			WORKSPACE_MCP_PORT: "8000",
			WORKSPACE_MCP_HOST: "0.0.0.0",
			// External OAuth: the bearer on each request is a Google access
			// token. Stateless mode keeps no credential store and no session.
			MCP_ENABLE_OAUTH21: "true",
			EXTERNAL_OAUTH21_PROVIDER: "true",
			WORKSPACE_MCP_STATELESS_MODE: "true",
			WORKSPACE_MCP_DISABLE_LOCAL_FILES: "true",
			WORKSPACE_EXTERNAL_URL: "http://127.0.0.1:8000",
			GOOGLE_OAUTH_CLIENT_ID: env.GOOGLE_OAUTH_CLIENT_ID.trim(),
			GOOGLE_OAUTH_CLIENT_SECRET: env.GOOGLE_OAUTH_CLIENT_SECRET.trim(),
		};
	}
}

export default {
	async fetch(request, env): Promise<Response> {
		if (!env.GOOGLE_OAUTH_CLIENT_ID?.trim() || !env.GOOGLE_OAUTH_CLIENT_SECRET?.trim()) {
			console.error("workspace-mcp: Google OAuth client is not configured");
			return new Response("Google Workspace is not configured.", { status: 503 });
		}

		const stub = env.WORKSPACE_MCP_CONTAINER.getByName(pickContainerInstance());
		return stub.fetch(request);
	},
} satisfies ExportedHandler<Env>;
