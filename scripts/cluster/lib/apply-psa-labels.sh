#!/bin/bash
set -euo pipefail

# apply-psa-labels.sh — Apply Pod Security Admission audit/warn labels to all 14 platform
# namespaces (KISA-POD-01).
#
# D-PSA-LABELS-14: 08-3-security.sh runs before 08-5-registry.sh (security-system),
# 09-istio-ambient.sh (istio-system), 11-keycloak.sh (iam), 11-4-keycloak-apiserver.sh
# (dev), and 14-gitops-bootstrap.sh (chaos-testing), so a single pass in 08-3 skipped 4
# namespaces and omitted chaos-testing. Centralizing the list here lets both
# 08-3-security.sh and 14-gitops-bootstrap.sh (and operators converging a live cluster)
# converge the exact same 14 namespaces without drift.
# audit/warn only — enforce is intentionally unset until workloads pass restricted/baseline
# acceptance (see docs/common/workload-security-profile.md).
# Cost: two idempotent label passes during Phase 2; escape hatch: re-run this script
# directly with KUBECONFIG set.

if [ -z "${KUBECONFIG:-}" ] && [ -r /home/vagrant/.kube/config-local ]; then
  export KUBECONFIG=/home/vagrant/.kube/config-local
fi

echo "=== Applying Pod Security Admission labels (KISA-POD-01) ==="
# audit/warn 모드만 사용 — enforce는 기존 워크로드 차단 위험이 있어 의도적으로 제외.
# 새 플랫폼 네임스페이스 추가 시 이 목록과 docs/common/workload-security-profile.md를 함께 갱신할 것.
for ns in iam devtools monitoring database dev default cilium-secrets; do
  if kubectl get ns "${ns}" >/dev/null 2>&1; then
    kubectl label ns "${ns}" \
      pod-security.kubernetes.io/audit=baseline \
      pod-security.kubernetes.io/warn=baseline --overwrite
  fi
done
# 호스트 리소스 접근이 필요한 네임스페이스는 privileged로 명시 (라벨 부재보다 명시가 낫다)
for ns in kube-system istio-system platform-system storage security-system nfs-quota-agent chaos-testing; do
  if kubectl get ns "${ns}" >/dev/null 2>&1; then
    kubectl label ns "${ns}" \
      pod-security.kubernetes.io/audit=privileged \
      pod-security.kubernetes.io/warn=privileged --overwrite
  fi
done
echo "PSA labels applied"
