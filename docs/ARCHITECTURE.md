# Architecture

The prototype has two Cloud Run services:

1. `gcp-control-plane-backend` runs FastAPI and ADK. It owns the managed MCP connections and the read-only policy.
2. `gcp-control-plane-frontend` runs Streamlit. It is the browser-facing application and invokes the backend using an identity token.

The backend is the security boundary. Streamlit is not trusted to decide which projects or operations are allowed.

The backend currently connects to these Google-managed MCP endpoints:

- Cloud Storage: `https://storage.googleapis.com/storage/mcp`
- BigQuery: `https://bigquery.googleapis.com/mcp`
- Compute Engine: `https://compute.googleapis.com/mcp`
- Resource Manager: `https://cloudresourcemanager.googleapis.com/mcp`

The prototype is read-only. It does not implement an executor, approval state, or mutation tools.
