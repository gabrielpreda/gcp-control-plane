# Project brief for future implementation sessions

## Objective

Build a secure, plain-English interface for inspecting and later managing authorized Google Cloud resources. The stack is Gemini on Vertex AI, Google ADK, Google-managed MCP servers, FastAPI, Streamlit, and Cloud Run.

## Binding decisions

- Use the existing Google-managed BigQuery MCP server; do not create a custom MCP server for the first version.
- Use separate Streamlit frontend and ADK/FastAPI backend Cloud Run services.
- First release is read-only.
- Use Cloud Run IAM/OIDC between frontend and backend.
- Do not deploy the administrative utility with unauthenticated access.
- Keep implementation enterprise-realistic: project allowlists, least privilege, structured audit logging, and explicit future approval workflows.
- Keep documentation aligned with the tested code.

## Current state

The initial scaffold contains Storage, BigQuery, Compute Engine, and Resource Manager managed MCP toolsets, a read-only ADK coordinator, FastAPI `/healthz` and `/query` endpoints, a Streamlit client, deployment skeleton, security documentation, and policy tests.

## Known limitation

Managed MCP toolsets are initialized at backend process startup with a token obtained from ADC. Before production use with long-lived instances or any mutation capability, implement and test token refresh/reconnection.

## Next implementation tasks

1. Install pinned dependencies in a clean virtual environment.
2. Verify the current ADK runner/session APIs against the installed ADK version.
3. Run the backend against a sandbox GCP project with read-only permissions.
4. Replace the placeholder `streamlit-user` with authenticated identity propagation.
5. Add structured audit records and Cloud Logging correlation IDs.
6. Add Cloud Run, Monitoring, Logging, and Security Command Center read-only agents.
7. Only then design the approval-based mutation executor.
