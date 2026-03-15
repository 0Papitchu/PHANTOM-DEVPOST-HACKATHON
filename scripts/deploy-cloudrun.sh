#!/usr/bin/env bash
# Deploy Phantom UI Navigator to Google Cloud Run
# Usage: ./scripts/deploy-cloudrun.sh [PROJECT_ID]
# Requires: gcloud CLI, Docker (or Cloud Build)

set -e

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null)}"
if [ -z "$PROJECT_ID" ]; then
  echo "Usage: $0 PROJECT_ID"
  echo "Or set default: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

SERVICE_NAME="phantom-ui"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "Building and pushing image to ${IMAGE}..."
gcloud builds submit --tag "${IMAGE}" --project "${PROJECT_ID}" .

echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --allow-unauthenticated \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID}" \
  --memory 2Gi \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 2

echo "Done. Service URL:"
gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --project "${PROJECT_ID}" --format='value(status.url)'
