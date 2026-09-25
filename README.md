# GCP Control Plane Assistant

Secure, read-only prototype for inspecting Google Cloud resources through a plain-English interface.

For the project setup and demonstration guide, see
[`CODELAB.md`](CODELAB.md).

The project uses Google-managed MCP servers, ADK, Gemini on Vertex AI, a FastAPI/ADK backend, and a Streamlit frontend. The first release deliberately excludes resource mutations. Any future mutation must be introduced through a separate approval and execution workflow.

## Scope of the prototype

- Cloud Storage inventory and configuration inspection
- Compute Engine inventory and instance inspection
- BigQuery metadata and safe analytical queries
- Cloud Run service and revision inspection
- Plain-English responses through Gemini
- Structured request logging
- Durable structured audit events with request/correlation IDs
- Separate frontend and backend services
- Cloud Run deployment with IAM-protected backend invocation

## Repository layout

```text
backend/
  agent.py          ADK Web entry point
  gcp_control_plane/
    agent.py       ADK coordinator and managed MCP toolsets
    api.py         FastAPI boundary and ADK runner
frontend/
  app.py           Streamlit interface
deploy/
  deploy.sh       deployment skeleton
CODELAB.md         project setup and demonstration guide
docs/
  ARCHITECTURE.md
  SECURITY.md
tests/
  test_policy.py
```

## Important security boundary

The model may inspect resources through MCP, but it is not an authorization mechanism. The Cloud Run service account, project allowlist, IAM policy, and application policy remain authoritative.

The prototype should be deployed with an authenticated backend. Do not use `--allow-unauthenticated` for an administrative utility.

## Configuration

See `.env.example`. The most important settings are:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `ALLOWED_PROJECT_IDS`
- `AGENT_MODEL`
- `BACKEND_URL` for the Streamlit service
- `PUBLIC_DEMO` for the optional public sandbox deployment mode
- `GRANT_DEMO_IAM` for optional sandbox IAM role setup
- `REQUIRE_AUTHENTICATED_IDENTITY` to require an IAP/proxy-provided user identity


`ALLOWED_PROJECT_IDS` is checked for explicitly named projects by the
application policy. Google Cloud IAM remains the authoritative authorization
boundary.

## Local development

Install backend dependencies and start the API:

```bash
cd backend
pip install -r requirements.txt
uvicorn gcp_control_plane.api:app --host 0.0.0.0 --port 8080
```

Start Streamlit separately:

```bash
cd frontend
pip install -r requirements.txt
streamlit run app.py
```

To try the agent through ADK Web, from the repository root run the equivalent
command supported by the installed ADK version, for example:

```bash
adk web backend
```

The repository includes `backend/agent.py` as the ADK Web entry point.

The local mode uses Application Default Credentials. Cloud Run uses the attached service account.

The backend also enforces the configured prompt length and request timeout.

## Deployment

Read `docs/SECURITY.md` and follow `CODELAB.md` before deployment. The intended order is backend first, then frontend. For a disposable sandbox, set `PUBLIC_DEMO=true` to make both `run.app` URLs directly reachable and `GRANT_DEMO_IAM=true` to grant the backend required read-only roles. These options must not be used for a production administrative utility. The frontend invokes the backend with an identity token.
