# GCP Control Plane Assistant — Codelab

This codelab prepares and demonstrates a small, read-only Google Cloud
operations assistant. The assistant uses Gemini on Vertex AI, Google ADK,
Google-managed MCP servers, FastAPI, Streamlit, and Cloud Run.

The codelab does not execute resource mutations. Resources are created by the
operator using normal Google Cloud controls, then inspected by the assistant.

## Codelab outcome

By the end, you will be able to:

- inspect a real sandbox project using natural language;
- see ADK Web and Streamlit use the same agent;
- observe MCP-backed answers for Storage, BigQuery, and Compute Engine;
- see a destructive request rejected by the read-only policy;
- understand the separation between Gemini, policy code, IAM, and cloud APIs.

## Preparation

### 1. Create the sandbox project

Create a fresh project with billing enabled. Record its project ID, project
number, billing account, and region. Use a dedicated project and
synthetic data only.

Enable the required APIs:

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  bigquery.googleapis.com \
  compute.googleapis.com \
  logging.googleapis.com \
  --project="$PROJECT_ID"
```

### 2. Create runtime identities

Create two service accounts:

```bash
gcloud iam service-accounts create gcp-control-plane-backend \
  --project="$PROJECT_ID"

gcloud iam service-accounts create gcp-control-plane-frontend \
  --project="$PROJECT_ID"
```

Grant the backend only the read and runtime permissions required by the
selected demo. Validate the exact MCP permissions in the sandbox before use.
Typical candidates are Vertex AI User, Storage Viewer, Compute Viewer,
BigQuery Data Viewer, BigQuery Metadata Viewer, BigQuery Job User, Resource
Cloud Run Viewer, and Logs Writer.

At minimum, grant the backend service account permission to call the selected
Vertex AI publisher model:

```bash
export BACKEND_SERVICE_ACCOUNT="gcp-control-plane-backend@$PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/aiplatform.user"
```

Grant the remaining read-only roles required by the resources used in the
demo:

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/mcp.toolUser"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/storage.viewer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/compute.viewer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/run.viewer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/bigquery.metadataViewer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
  --role="roles/bigquery.jobUser"
```

Verify that the Vertex AI role is attached to the backend service account
before deploying:

```bash
gcloud projects get-iam-policy "$PROJECT_ID" \
  --flatten="bindings[].members" \
  --filter="bindings.members:$BACKEND_SERVICE_ACCOUNT AND bindings.role:roles/aiplatform.user" \
  --format="table(bindings.role,bindings.members)"
```

The first real backend request is also an end-to-end verification of
`aiplatform.endpoints.predict`. For a detailed IAM diagnosis, use Policy
Troubleshooter with the backend service account as the principal.

Grant the frontend service account Cloud Run Invoker on the backend after the
backend is deployed. Do not grant either runtime identity Owner, Editor,
Storage Admin, or Compute Admin.

### 3. Create the demo resources

Create these resources in the dedicated project:

- a bucket named `gcd-demo-bucket-<suffix>` with a few harmless objects;
- a BigQuery dataset containing small synthetic or sample tables;
```bash
export BIGQUERY_PROJECT="your-sandbox-project"
export BIGQUERY_LOCATION="US"

bq --project_id="$BIGQUERY_PROJECT" mk -f \
  --dataset \
  --location="$BIGQUERY_LOCATION" \
  cymbal_pets

for table in products customers orders order_items; do
  bq --project_id="$BIGQUERY_PROJECT" query \
    --location="$BIGQUERY_LOCATION" \
    --nouse_legacy_sql \
    "LOAD DATA OVERWRITE cymbal_pets.${table} FROM FILES(
      format = 'AVRO',
      uris = ['gs://sample-data-and-media/cymbal-pets/tables/${table}/*.avro']);"
done
```
- one small Compute Engine instance named `gcd-demo-vm`;
```bash
gcloud compute instances create gcp-demo-vm \
    --zone=us-central1-a \
    --machine-type=e2-micro \
    --provisioning-model=SPOT \
    --image-family=debian-12 \
    --image-project=debian-cloud
```
- labels such as `environment=demo`, `owner=demo`, and `purpose=assistant`.
```bash
gcloud compute instances add-labels gcp-demo-vm \
    --zone=us-central1-a \
    --labels=environment=demo,owner=demo,purpose=assistant
```

Use different bucket settings where useful—for example, one lifecycle rule or
versioning enabled—so the assistant has something meaningful to describe.

Stop the VM when it is not needed to control cost. Optionally create one bucket
or BigQuery table interactively, then ask the assistant to find it.




If the dataset already exists, the create command may report that it exists;
the load commands can still be run. Confirm that the sample bucket is
available and that the dataset location matches the load job location before
using this path.

### 4. Prepare local configuration

Copy `.env.example` to `.env` and set the sandbox project ID:

```bash
cp .env.example .env
```

Set Application Default Credentials for the operator account:

```bash
gcloud auth application-default login
gcloud config set project "$PROJECT_ID"
```

For local testing, ensure the operator account can use Vertex AI and read the
demo resources. Cloud Run uses the attached backend service account instead.

## Local validation

Create a clean virtual environment and install the backend dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Start the API:

```bash
uvicorn gcp_control_plane.api:app --app-dir backend --host 0.0.0.0 --port 8080
```

In a second terminal, verify:

```bash
curl http://localhost:8080/healthz
```

Then test `/query` with the example prompts below.

## ADK Web validation

The repository contains `backend/agent.py` as the ADK Web entry point. From
the repository root, try:

```bash
adk web backend
```

If the installed ADK version uses the current agents CLI playground workflow,
use its equivalent playground command and open the displayed local URL.

Run at least these prompts:

```text
List the Cloud Storage buckets in the sandbox project.
```

```text
List the BigQuery datasets and tables in the demo dataset.
```

```text
Show the Compute Engine instances, zones, labels, and status.
```

Keep a terminal/API fallback ready in case the browser playground has a
version-specific issue.

## Deploy to Cloud Run

Set deployment variables in the shell:

```bash
export PROJECT_ID="your-sandbox-project"
export REGION="us-central1"
export BACKEND_SERVICE_ACCOUNT="gcp-control-plane-backend@$PROJECT_ID.iam.gserviceaccount.com"
export FRONTEND_SERVICE_ACCOUNT="gcp-control-plane-frontend@$PROJECT_ID.iam.gserviceaccount.com"
export ALLOWED_PROJECT_IDS="$PROJECT_ID"
export PUBLIC_DEMO="true"
export GRANT_DEMO_IAM="true"
export REQUIRE_AUTHENTICATED_IDENTITY="false"
```

Deploy the services:

```bash
./deploy/deploy.sh
```

`PUBLIC_DEMO=true` makes both Cloud Run services directly reachable through
their `run.app` URLs without a load balancer or Cloud Run proxy. Use this only
with a disposable sandbox project. For a protected deployment, omit the
variable or set it to `false`; the services will use IAM authentication and
`internal-and-cloud-load-balancing` ingress.

`GRANT_DEMO_IAM=true` grants the backend service account the read-only product
roles and MCP Tool User role required by the demo. It is opt-in and intended
only for a disposable sandbox. Omit it when IAM is managed separately.

Production deployments should leave `REQUIRE_AUTHENTICATED_IDENTITY=true`.
The frontend forwards the identity supplied by IAP or another identity-aware
proxy to the authenticated backend. Local development may set it to `false`,
which uses the explicit `local-user` fallback.

Verify that:

- both Cloud Run services are ready;
- services use the intended ingress and authentication mode;
- the frontend service account has `roles/run.invoker` on the backend;
- the backend can call Vertex AI and the managed MCP endpoints;
- the frontend can invoke the backend;
- request logs are visible in Cloud Logging.

The script prints both service URLs. The backend root URL is an API service and
has no homepage; use `/healthz` to test it. The frontend URL opens the
Streamlit interface.

Cloud Run service-to-service calls use an OIDC identity token and a Cloud Run
Invoker binding. The token audience must be the backend service URL.

## Demonstration flow

### Context

Introduce the problem and the safety model:

> Gemini interprets the request; policy code decides what is allowed; IAM and
> MCP tools provide the actual cloud access.

### Show the demo resources

Open the Cloud Console and show the bucket, BigQuery dataset, and VM. Explain
that these resources were created normally and are now being inspected by the
assistant.

### ADK Web

Run the three inventory prompts and show the agent/tool interaction.

### Streamlit

Open the deployed Streamlit interface and run:

```text
Give me an inventory of the demo project covering Cloud Storage, BigQuery,
and Compute Engine. Include names, locations, labels, and status where
available.
```

Expand the **Tool activity** panel below the answer to inspect the MCP tool
calls and bounded JSON arguments/results used to produce the response.

### Small resource change outside the agent

Create one bucket or BigQuery table manually, then ask:

```text
Find the resource I just created and describe its configuration.
```

This demonstrates that the assistant can observe a changing environment while
remaining read-only.

### Safety boundary

Ask:

```text
Delete the temporary demo bucket.
```

Show that the application rejects the request before the agent performs a
mutation.

### Deployment and IAM

Show Cloud Run, service accounts, the Invoker binding, and backend logs.

### Roadmap

Mention project/resource authorization, structured audit records, Cloud Run
and Monitoring agents, BigQuery cost controls, approval workflows, and a
separate mutation executor as future work.

## Final rehearsal checklist

- [ ] Fresh sandbox project works.
- [ ] Billing and quotas are confirmed.
- [ ] All APIs are enabled.
- [ ] Service accounts and IAM bindings are tested.
- [ ] Storage, BigQuery, and Compute resources exist.
- [ ] Local backend returns a successful answer.
- [ ] ADK Web works with the installed ADK version.
- [ ] Cloud Run deployment succeeds.
- [ ] Streamlit invokes the private backend.
- [ ] Destructive request is rejected.
- [ ] Cloud Logging shows request IDs.
- [ ] Demo resources are stopped or deleted when no longer needed.
