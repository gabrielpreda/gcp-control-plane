# Security and deployment guidance

## Required controls

- Deploy the backend with a dedicated service account.
- Grant only the viewer, BigQuery read, Vertex AI, logging, and MCP tool permissions required by the enabled capabilities.
- Restrict `ALLOWED_PROJECT_IDS`; do not leave it empty in production.
- Set `REQUIRE_AUTHENTICATED_IDENTITY=true` in production and place the frontend
  behind IAP or an equivalent identity-aware proxy.
- Protect the backend with Cloud Run IAM. Grant `roles/run.invoker` only to the frontend service account.
- Do not deploy either service with `--allow-unauthenticated`.
- For browser access, place the frontend behind Identity-Aware Proxy or an equivalent identity-aware gateway.
- Prefer `internal-and-cloud-load-balancing` ingress and disable the default Cloud Run URL when the load-balancer setup is complete.
- Never store service-account keys in the repository or container.
- Grant `roles/run.viewer` only when Cloud Run inventory is enabled.
- Treat Cloud Logging audit records as the durable request trail. They contain
  correlation IDs and tool names, but intentionally exclude prompts, tool
  arguments, tool responses, and credentials.

## MCP token lifecycle

Managed MCP toolsets use ADK's dynamic header provider. The provider reuses ADC
credentials in memory and refreshes them when expired before creating an MCP
session. This avoids relying on Cloud Run revision recycling for token expiry.
Reconnection behavior should still be exercised against each managed MCP during
integration testing.

## Future mutation controls

Mutations must use a separate executor identity and accept only validated structured change plans. Every change should be re-read before execution, require explicit approval, be idempotent, and generate a durable audit record.
