#!/usr/bin/env bash
set -euo pipefail

DEBUG_IMAGE="${NODE_DEBUG_IMAGE:-ubuntu:24.04}"
FAILED=0
if ! nodes="$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"; then
  echo 'FAIL: unable to list Kubernetes nodes' >&2
  exit 1
fi
if [ -z "${nodes}" ]; then
  echo 'FAIL: Kubernetes returned no nodes to verify' >&2
  exit 1
fi

cleanup_debug_pods() {
  local pod
  kubectl -n kube-system get pods -o name 2>/dev/null \
    | grep "^pod/node-debugger-$1-" \
    | while IFS= read -r pod; do kubectl -n kube-system delete "${pod}" --wait=false >/dev/null; done || true
}

while IFS= read -r node; do
  [ -n "${node}" ] || continue
  # kubectl debug returns 0 even when the container exits non-zero (verified live),
  # so the result is a sentinel the node prints only when both checks hold.
  # -i --quiet attaches so the output is captured; kube-system is exempt from the
  # Kyverno host-namespace/privileged policies.
  output="$(kubectl debug -n kube-system -i --quiet "node/${node}" --profile=sysadmin \
    --image="${DEBUG_IMAGE}" -- chroot /host sh -c '
      systemctl is-active --quiet rpc-statd.service \
        && test -L /etc/systemd/system/multi-user.target.wants/rpc-statd.service \
        && echo STATD_BOOT_OK
    ' 2>&1 || true)"
  if printf '%s\n' "${output}" | grep -qx 'STATD_BOOT_OK'; then
    printf 'PASS %s: rpc-statd active and wanted by multi-user.target\n' "${node}"
  else
    printf 'FAIL %s: rpc-statd is not active and enabled for boot: %s\n' "${node}" "${output}" >&2
    FAILED=$((FAILED + 1))
  fi
  cleanup_debug_pods "${node}"
done <<< "${nodes}"

if [ "${FAILED}" -gt 0 ]; then
  exit 1
fi
echo 'PASS: rpc-statd is active and enabled for boot on every node'
