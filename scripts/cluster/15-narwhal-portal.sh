#!/bin/bash
# 15-narwhal-portal.sh
# narwhal-portal Deployment 배포 확인 (non-critical)
#
# 실행 시점: 06-phase2-start.sh 에서 14-gitops-bootstrap.sh 직후.
# 역할:
#   - Harbor의 library/narwhal-portal:latest 이미지 존재 여부를 확인한다.
#   - 이미지가 있으면 narwhal-portal Deployment를 rollout restart하여 최신 이미지를 반영.
#   - 이미지가 없으면 호스트 빌드 안내 메시지를 출력하고 0으로 정상 종료
#     (포털 이미지는 VM 내부에서 빌드 불가 — 소스는 호스트에만 있음).
#
# 멱등성: rollout restart는 이미 최신 상태여도 무해.
# 의존 스크립트:
#   08-5-registry.sh — Harbor 기동
#   13-2-narwhal-portal-bindings.sh — narwhal-portal-secrets 생성
#   14-gitops-bootstrap.sh — ArgoCD App-of-Apps → narwhal-portal.yaml 배포
set -euo pipefail

echo "============================================"
echo "15: narwhal-portal Deployment 확인"
echo "============================================"

export KUBECONFIG=/home/vagrant/.kube/config-local

NAMESPACE="devtools"
DEPLOYMENT="narwhal-portal"
HARBOR_HOST="harbor.local.narwhal.io"
HARBOR_PROJECT="library"
HARBOR_REPO="narwhal-portal"

# Harbor core ClusterIP 조회 (harbor-core Service, devtools namespace)
HARBOR_CORE_IP=$(kubectl get svc harbor-core -n devtools \
  -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")

check_image_exists() {
  if [ -z "${HARBOR_CORE_IP}" ]; then
    echo "WARN: harbor-core Service not found in devtools namespace; skipping image check"
    return 1
  fi

  # Harbor API v2 — artifacts endpoint (admin password: devtools/harbor-secrets)
  HARBOR_ADMIN_PASSWORD=$(kubectl get secret harbor-secrets -n devtools \
    -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' 2>/dev/null | base64 -d || echo "")
  if [ -z "${HARBOR_ADMIN_PASSWORD}" ]; then
    HARBOR_ADMIN_PASSWORD="Harbor12345"  # chart default
  fi

  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -u "admin:${HARBOR_ADMIN_PASSWORD}" \
    "http://${HARBOR_CORE_IP}/api/v2.0/projects/${HARBOR_PROJECT}/repositories/${HARBOR_REPO}/artifacts" \
    --max-time 10 2>/dev/null || echo "000")

  if [ "${HTTP_CODE}" = "200" ]; then
    return 0
  else
    return 1
  fi
}

#=========================================
# 이미지 존재 확인
#=========================================
echo "Harbor에서 ${HARBOR_HOST}/${HARBOR_PROJECT}/${HARBOR_REPO}:latest 이미지 확인 중..."

if check_image_exists; then
  echo "이미지 확인됨 — narwhal-portal Deployment 재기동 중..."

  # Deployment가 아직 없으면 ArgoCD 동기화를 기다린다 (최대 3분)
  deploy_ready="false"
  for attempt in $(seq 1 18); do
    if kubectl get deployment "${DEPLOYMENT}" -n "${NAMESPACE}" &>/dev/null; then
      deploy_ready="true"
      break
    fi
    echo "  narwhal-portal Deployment 대기 중... (${attempt}/18)"
    sleep 10
  done

  if [ "${deploy_ready}" = "true" ]; then
    kubectl rollout restart deployment/"${DEPLOYMENT}" -n "${NAMESPACE}"
    echo "  rollout restart 완료. 상태 확인:"
    kubectl rollout status deployment/"${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=120s || \
      echo "  WARN: rollout 타임아웃 (계속 진행)"
  else
    echo "WARN: narwhal-portal Deployment가 아직 생성되지 않음 (ArgoCD sync 지연 가능)"
    echo "  나중에 수동 확인: kubectl rollout restart deployment/narwhal-portal -n devtools"
  fi
else
  echo ""
  echo "================================================================"
  echo "  NOTICE: Harbor에 narwhal-portal 이미지가 없습니다."
  echo ""
  echo "  포털 이미지는 VM 내부에서 빌드할 수 없습니다 (소스가 호스트에만 있음)."
  echo "  호스트에서 아래 명령을 실행하여 빌드 + Harbor 푸시 후 재배포하세요:"
  echo ""
  echo "    cd /path/to/narwhal-portal"
  echo "    make all          # docker build + push to ${HARBOR_HOST}/${HARBOR_PROJECT}/${HARBOR_REPO}"
  echo ""
  echo "  푸시 후 포털을 재기동하려면:"
  echo "    kubectl rollout restart deployment/narwhal-portal -n devtools"
  echo "================================================================"
  echo ""
  echo "15-narwhal-portal.sh: 이미지 없음 — 정상 종료 (non-critical)"
fi

echo "============================================"
echo "15: narwhal-portal 확인 완료"
echo "============================================"
