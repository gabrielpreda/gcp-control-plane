#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:=us-central1}"
: "${BACKEND_SERVICE_ACCOUNT:?Set BACKEND_SERVICE_ACCOUNT}"
: "${FRONTEND_SERVICE_ACCOUNT:?Set FRONTEND_SERVICE_ACCOUNT}"
: "${ALLOWED_PROJECT_IDS:?Set ALLOWED_PROJECT_IDS}"
: "${AGENT_MODEL:=gemini-2.5-flash}"
: "${REQUEST_TIMEOUT_SECONDS:=180}"
: "${PUBLIC_DEMO:=false}"
: "${GRANT_DEMO_IAM:=false}"
: "${REQUIRE_AUTHENTICATED_IDENTITY:=true}"

if [[ "$PUBLIC_DEMO" == "true" ]]; then
  # Intended only for an isolated demo project. This makes both run.app URLs
  # directly reachable without a load balancer or Cloud Run proxy.
  BACKEND_ACCESS_FLAGS=(--allow-unauthenticated --ingress=all)
  FRONTEND_ACCESS_FLAGS=(--allow-unauthenticated --ingress=all)
else
  BACKEND_ACCESS_FLAGS=(--no-allow-unauthenticated --ingress=internal-and-cloud-load-balancing)
  FRONTEND_ACCESS_FLAGS=(--no-allow-unauthenticated --ingress=internal-and-cloud-load-balancing)
fi

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

if [[ "$GRANT_DEMO_IAM" == "true" ]]; then
  # Opt-in grants for a disposable sandbox. Manage production IAM separately.
  for role in \
    roles/mcp.toolUser \
    roles/aiplatform.user \
    roles/storage.viewer \
    roles/storage.objectViewer \
    roles/compute.viewer \
    roles/run.viewer \
    roles/bigquery.metadataViewer \
    roles/bigquery.dataViewer \
    roles/bigquery.jobUser; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$BACKEND_SERVICE_ACCOUNT" \
      --role="$role" \
      --quiet
  done
fi

gcloud run deploy gcp-control-plane-backend \
  --source backend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --service-account="$BACKEND_SERVICE_ACCOUNT" \
  "${BACKEND_ACCESS_FLAGS[@]}" \
  --set-env-vars="^|^GOOGLE_CLOUD_PROJECT=$PROJECT_ID|GOOGLE_CLOUD_LOCATION=$REGION|GOOGLE_GENAI_USE_VERTEXAI=true|AGENT_MODEL=$AGENT_MODEL|ALLOWED_PROJECT_IDS=$ALLOWED_PROJECT_IDS|REQUEST_TIMEOUT_SECONDS=$REQUEST_TIMEOUT_SECONDS|REQUIRE_AUTHENTICATED_IDENTITY=$REQUIRE_AUTHENTICATED_IDENTITY"

BACKEND_URL="$(gcloud run services describe gcp-control-plane-backend --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"

gcloud run services add-iam-policy-binding gcp-control-plane-backend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --member="serviceAccount:$FRONTEND_SERVICE_ACCOUNT" \
  --role="roles/run.invoker"

gcloud run deploy gcp-control-plane-frontend \
  --source frontend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --service-account="$FRONTEND_SERVICE_ACCOUNT" \
  "${FRONTEND_ACCESS_FLAGS[@]}" \
  --set-env-vars="BACKEND_URL=$BACKEND_URL"

echo "Backend: $BACKEND_URL"
FRONTEND_URL="$(gcloud run services describe gcp-control-plane-frontend --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
echo "Frontend: $FRONTEND_URL"
if [[ "$PUBLIC_DEMO" == "true" ]]; then
  echo "WARNING: PUBLIC_DEMO=true exposes both services publicly. Use only with a disposable sandbox project."
fi
if [[ "$GRANT_DEMO_IAM" == "true" ]]; then
  echo "WARNING: GRANT_DEMO_IAM=true granted demo read/runtime roles to the backend service account."
fi
