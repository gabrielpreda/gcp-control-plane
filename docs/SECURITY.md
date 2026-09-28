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
  correlation IDs, model name/version, input/reasoning/output token counts,
  call time, tool names, and guard decisions, but
  intentionally exclude prompts, tool arguments, tool responses, and
  credentials.
- Keep the deterministic application tool guard enabled. It runs in ADK's
  `before_tool_callback`, so a mutation-like tool call is returned as a blocked
  result before the MCP tool executes. This is defense in depth; IAM remains
  authoritative.

## MCP token lifecycle

Managed MCP toolsets use ADK's dynamic header provider. The provider reuses ADC
credentials in memory and refreshes them when expired before creating an MCP
session. This avoids relying on Cloud Run revision recycling for token expiry.
Reconnection behavior should still be exercised against each managed MCP during
integration testing.

## Future mutation controls

Mutations must use a separate executor identity and accept only validated structured change plans. Every change should be re-read before execution, require explicit approval, be idempotent, and generate a durable audit record.

## Model Armor

Model Armor is supported as an optional input/output screening layer. Set
`MODEL_ARMOR_TEMPLATE` to a full template resource name and grant the backend
service account `roles/modelarmor.user` (and viewer access to the template).
`MODEL_ARMOR_LOCATION` selects the regional endpoint; `MODEL_ARMOR_FAIL_CLOSED`
defaults to `true`. Use separate templates for user prompts and model
responses where possible. Model Armor can detect prompt injection, sensitive
data, unsafe URLs, and harmful content, but it does not authorize Google Cloud
operations or replace the tool guard, IAM, project allowlist, or read-only
OAuth scopes.

For direct `adk web` testing, Model Armor is only active when
`MODEL_ARMOR_TEMPLATE` is configured in the environment loaded by ADK Web.
Without that setting, the deterministic application policy still blocks
destructive requests before the agent runs, but no Model Armor call is made.
