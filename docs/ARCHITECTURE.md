# Architecture

The prototype has two Cloud Run services:

1. `gcp-control-plane-backend` runs FastAPI and ADK. It owns the managed MCP connections and the read-only policy.
2. `gcp-control-plane-frontend` runs Streamlit. It is the browser-facing application and invokes the backend using an identity token.

The backend is the security boundary. Streamlit is not trusted to decide which projects or operations are allowed.

The backend currently connects to these Google-managed MCP endpoints:

- Cloud Storage: `https://storage.googleapis.com/storage/mcp`
- BigQuery: `https://bigquery.googleapis.com/mcp`
- Compute Engine: `https://compute.googleapis.com/mcp`
- Cloud Run: `https://run.googleapis.com/mcp`

The backend uses the ADK 1.x hierarchical model: one coordinator `Agent` with
four domain-specialized sub-agents for Storage, BigQuery, Compute Engine, and
Cloud Run. Each sub-agent owns only its domain's MCP toolset. Resource Manager
and Cloud Asset Inventory are intentionally disabled for this iteration. The
prototype is read-only. It does not implement an executor, approval state, or
mutation tools.

The frontend obtains the end-user identity from IAP or an equivalent
identity-aware proxy and forwards it to the IAM-authenticated backend. The
backend uses that identity as the ADK session user instead of a fixed shared
user ID.

Each request receives a request ID and correlation ID. Structured lifecycle
events are emitted to Cloud Logging, including policy decisions, identity
rejections, timeouts, tool names, and completion status. Prompt contents and
tool payloads are excluded from audit records.
