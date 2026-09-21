#!/usr/bin/env bash
# API를 Cloud Run에 올린다 (docs/07_deployment.md)
#
# 벡터 색인이 저장소에 없으므로 **이 스크립트는 로컬에서 돌린다**. CI에서는 돌지 않는다.
#
#   PROJECT_ID=... DATABASE_URL=... WEB_ORIGIN=... ./deploy/deploy_api.sh
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID를 설정하세요 (예: export PROJECT_ID=cdq-chatbot)}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-cdq-api}"
REPO="${REPO:-cdq}"
TAG="${TAG:-$(date +%Y%m%d-%H%M)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/api:${TAG}"

DATABASE_URL="${DATABASE_URL:?Neon 연결 문자열이 필요합니다}"
WEB_ORIGIN="${WEB_ORIGIN:?프런트 도메인이 필요합니다 (예: https://cdq.vercel.app)}"
ADMIN_ID="${ADMIN_ID:-admin}"
ADMIN_PW="${ADMIN_PW:-admin}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "▶ 이미지 빌드: ${IMAGE}"
docker build -t "${IMAGE}" "${ROOT}/apps/api"

echo "▶ 푸시"
docker push "${IMAGE}"

echo "▶ 배포"
# --timeout은 실행 제한(60초)보다 넉넉히 둔다. SSE 응답이 그 안에 끝나야 한다
gcloud run deploy "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2 \
  --concurrency 20 \
  --timeout 300 \
  --set-env-vars "DATABASE_URL=${DATABASE_URL},ALLOWED_ORIGINS=${WEB_ORIGIN},ADMIN_ID=${ADMIN_ID},ADMIN_PW=${ADMIN_PW},COOKIE_SAMESITE=none,COOKIE_SECURE=true,NATIVE_MODE=paper,TOOLS_ENABLED=false"

URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
echo "▶ 배포 완료: ${URL}"
echo "▶ 확인: curl ${URL}/api/health"
