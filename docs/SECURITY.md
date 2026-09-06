# Security and deployment guidance

## Required controls

- Deploy the backend with a dedicated service account.
- Grant only the viewer, BigQuery read, Vertex AI, logging, and MCP tool permissions required by the enabled capabilities.
- Restrict `ALLOWED_PROJECT_IDS`; do not leave it empty in production.
- Protect the backend with Cloud Run IAM. Grant `roles/run.invoker` only to the frontend service account.
- Do not deploy either service with `--allow-unauthenticated`.
- For browser access, place the frontend behind Identity-Aware Proxy or an equivalent identity-aware gateway.
- Prefer `internal-and-cloud-load-balancing` ingress and disable the default Cloud Run URL when the load-balancer setup is complete.
- Never store service-account keys in the repository or container.

## Current prototype limitation

The managed MCP toolsets are initialized at process startup, using an ADC access token. This matches the existing prototypes and is acceptable only for this read-only first iteration. Before adding mutations or operating long-lived instances, replace this with an explicit refresh/reconnection strategy and test token expiry.

## Future mutation controls

Mutations must use a separate executor identity and accept only validated structured change plans. Every change should be re-read before execution, require explicit approval, be idempotent, and generate a durable audit record.
