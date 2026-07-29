#!/bin/bash
# 15-narwhal-portal.sh
# narwhal-portal Deployment 배포 확인 (non-critical)
#
# 실행 시점: 06-phase2-start.sh 에서 14-gitops-bootstrap.sh 직후.
# 역할:
#   - narwhal-portal 이미지는 ghcr.io/dasomel/narwhal-portal:<pinned> 에서 pull된다
#     (gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml).
#     ArgoCD App-of-Apps가 Deployment를 동기화하므로 여기서는 이미지를 빌드하지 않고,
#     Deployment가 Ready가 될 때까지 대기(readiness gate)만 한다.
#   - (개발자용) 커스텀 포털 이미지를 in-cluster로 빌드하려면 Kaniko 셀프서비스
#     도구를 쓴다: narwhal-portal repo의 scripts/kaniko-build.sh 가 소스를 in-cluster
#     Gitea에 push → Kaniko Job으로 빌드 → Harbor에 push한다. 그 뒤 gitops 이미지
#     태그를 그 이미지로 바꾸면 ArgoCD가 배포한다. 자세한 내용: docs/common/developer-kaniko-builds.md
#
# 멱등성: 상태 확인만 하므로 재실행 무해.
# 의존 스크립트:
#   13-2-narwhal-portal-bindings.sh — narwhal-portal-secrets 생성
#   14-gitops-bootstrap.sh — ArgoCD App-of-Apps → narwhal-portal.yaml 배포
set -euo pipefail

echo "============================================"
echo "15: narwhal-portal Deployment 확인"
echo "============================================"

export KUBECONFIG=/home/vagrant/.kube/config-local

NAMESPACE="devtools"
DEPLOYMENT="narwhal-portal"

# 포털 이미지는 GHCR(ghcr.io/dasomel/narwhal-portal, public)에서 pull되므로
# in-cluster 빌드가 필요 없다. ArgoCD가 Deployment를 만들 때까지 대기(최대 3분).
deploy_ready="false"
for attempt in $(seq 1 18); do
  if kubectl get deployment "${DEPLOYMENT}" -n "${NAMESPACE}" &>/dev/null; then
    deploy_ready="true"
    break
  fi
  echo "  narwhal-portal Deployment 대기 중 (ArgoCD sync)... (${attempt}/18)"
  sleep 10
done

if [ "${deploy_ready}" = "true" ]; then
  echo "  Deployment 확인 — GHCR 이미지 pull + rollout 상태 확인:"
  kubectl rollout status deployment/"${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=180s || \
    echo "  WARN: rollout 타임아웃 (계속 진행 — 이미지 pull 지연 가능, ArgoCD가 재시도)"
else
  echo "WARN: narwhal-portal Deployment가 아직 생성되지 않음 (ArgoCD sync 지연 가능)"
  echo "  나중에 수동 확인: kubectl get application narwhal-portal -n devtools"
fi

echo ""
echo "  (개발자용) 커스텀 포털 이미지를 in-cluster Kaniko로 빌드하려면:"
echo "    cd narwhal-portal && ./scripts/kaniko-build.sh    # 소스→Gitea→Kaniko→Harbor push"
echo "    → 이후 gitops 포털 이미지 태그를 그 이미지로 교체하면 ArgoCD가 배포"
echo "    자세한 내용: docs/common/developer-kaniko-builds.md"

echo "============================================"
echo "15: narwhal-portal 확인 완료"
echo "============================================"
