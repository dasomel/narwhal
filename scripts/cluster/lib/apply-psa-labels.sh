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

REQUIRE_ALL=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --require-all) REQUIRE_ALL=1; shift ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "${KUBECONFIG:-}" ] && [ -r /home/vagrant/.kube/config-local ]; then
  export KUBECONFIG=/home/vagrant/.kube/config-local
fi

# Distinguish "namespace does not exist yet" (NotFound) from API/permission errors (Forbidden,
# Unauthorized, connection refused, etc.) and fail loudly on the latter.
is_not_found_error() {
  local out="$1"
  if printf '%s' "${out}" | grep -qiE 'notfound|\(notfound\)|not found'; then
    return 0
  fi
  return 1
}

ns_exists_or_not_found() {
  local ns="$1"
  local out=""
  local rc=0
  out="$(kubectl get ns "${ns}" -o name 2>&1)" || rc=$?
  if [ "${rc}" -eq 0 ]; then
    return 0
  fi
  if is_not_found_error "${out}"; then
    return 1
  fi
  echo "ERROR: kubectl get ns ${ns} failed with non-NotFound error (rc=${rc}): ${out}" >&2
  exit 1
}

labelled_ns=()
skipped_ns=()
mismatched_ns=()
convergence_targets=()

check_converged_profile() {
  local ns="$1"
  local expected="$2"
  local out=""
  local rc=0
  out="$(kubectl get ns "${ns}" \
    -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/audit}:{.metadata.labels.pod-security\.kubernetes\.io/warn}' 2>&1)" || rc=$?
  if [ "${rc}" -ne 0 ]; then
    if is_not_found_error "${out}"; then
      skipped_ns+=("${ns}")
      return 0
    fi
    echo "ERROR: convergence check failed on namespace ${ns} (rc=${rc}): ${out}" >&2
    exit 1
  fi
  if [ "${out}" = "${expected}:${expected}" ]; then
    labelled_ns+=("${ns}")
  else
    mismatched_ns+=("${ns}(got=${out},expected=${expected}:${expected})")
  fi
}

echo "=== Applying Pod Security Admission labels (KISA-POD-01) ==="
# audit/warn 모드만 사용 — enforce는 기존 워크로드 차단 위험이 있어 의도적으로 제외.
# 새 플랫폼 네임스페이스 추가 시 이 목록과 docs/common/workload-security-profile.md를 함께 갱신할 것.
for ns in iam devtools monitoring database dev default cilium-secrets; do
  convergence_targets+=("${ns}:baseline")
  if ns_exists_or_not_found "${ns}"; then
    kubectl label ns "${ns}" \
      pod-security.kubernetes.io/audit=baseline \
      pod-security.kubernetes.io/warn=baseline --overwrite
  else
    echo "SKIP (namespace not created yet): ${ns}"
  fi
done
# 호스트 리소스 접근이 필요한 네임스페이스는 privileged로 명시 (라벨 부재보다 명시가 낫다)
for ns in kube-system istio-system platform-system storage security-system nfs-quota-agent chaos-testing; do
  convergence_targets+=("${ns}:privileged")
  if ns_exists_or_not_found "${ns}"; then
    kubectl label ns "${ns}" \
      pod-security.kubernetes.io/audit=privileged \
      pod-security.kubernetes.io/warn=privileged --overwrite
  else
    echo "SKIP (namespace not created yet): ${ns}"
  fi
done

for target in "${convergence_targets[@]}"; do
  check_converged_profile "${target%%:*}" "${target#*:}"
done

if [ "${#mismatched_ns[@]}" -gt 0 ]; then
  echo "ERROR: PSA convergence failed; label mismatch on: ${mismatched_ns[*]}" >&2
  exit 1
fi

if [ "${#labelled_ns[@]}" -gt 0 ]; then
  echo "PSA labels applied and verified (${#labelled_ns[@]}/14): ${labelled_ns[*]}"
else
  echo "PSA convergence: 0/14 target namespaces exist yet"
fi

if [ "${#skipped_ns[@]}" -gt 0 ]; then
  echo "PSA skipped (${#skipped_ns[@]}/14 namespaces do not exist yet): ${skipped_ns[*]}"
  if [ "${REQUIRE_ALL}" -eq 1 ]; then
    echo "ERROR: --require-all specified and ${#skipped_ns[@]} namespace(s) are missing: ${skipped_ns[*]}" >&2
    exit 1
  fi
fi
