#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# 15-idp-portal.sh
# Phase: IDP Portal 배포 준비
# - Keycloak idp-portal 클라이언트는 11-3-keycloak-clients.sh에서 생성됨
# - 이 스크립트는 ArgoCD가 idp-portal-k8s.yaml을 sync한 이후 실행
# Depends on: 11-3-keycloak-clients.sh (idp-portal-secrets 생성), 14-gitops-bootstrap.sh

export KUBECONFIG=/home/vagrant/.kube/config-local
DOMAIN="${DOMAIN:-local.narwhal.io}"

echo "=== IDP Portal Deployment Check ==="

# idp-portal-secrets는 11-3-keycloak-clients.sh에서 생성됨
if kubectl get secret idp-portal-secrets -n devtools &>/dev/null; then
  echo "[OK] idp-portal-secrets exists in devtools"
else
  echo "WARN: idp-portal-secrets not found. Run 11-3-keycloak-clients.sh first."
fi

# Harbor 이미지 존재 확인
echo ""
echo "NOTE: Before idp-portal pod can start, build and push the Docker image:"
echo "  cd idp-portal/"
echo "  docker build -t harbor.${DOMAIN}/library/idp-portal:latest ."
echo "  docker login harbor.${DOMAIN} -u admin"
echo "  docker push harbor.${DOMAIN}/library/idp-portal:latest"
echo ""
echo "Access: https://portal.${DOMAIN}"
echo ""
echo "=== IDP Portal Deployment Check Complete ==="
