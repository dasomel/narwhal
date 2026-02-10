#!/usr/bin/env bash
# SessionStart Hook: 프로젝트 컨텍스트 자동 로드
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CACHE_DIR="${PROJECT_DIR}/.claude/cache"

echo "=== Narwhal Project Context ==="
echo ""
echo "Project: Vagrant 기반 Kubernetes IDP Platform"
echo "K8s Version: $(grep 'K8S_VERSION' "${PROJECT_DIR}/Vagrantfile" | head -1 | cut -d'"' -f2)"
echo ""
echo "Key Files:"
echo "  - Vagrantfile: 클러스터 설정"
echo "  - VERSIONS.md: 컴포넌트 버전"
echo "  - scripts/: 프로비저닝 스크립트"
echo "  - gitops/: ArgoCD 앱 매니페스트"
echo ""
echo "Commands:"
echo "  vagrant up        # 클러스터 생성"
echo "  vagrant ssh master # Master 접속"
echo "  vagrant destroy -f # 클러스터 삭제"
echo ""
echo "================================"
