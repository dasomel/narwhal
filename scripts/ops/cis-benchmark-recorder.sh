#!/usr/bin/env bash
set -euo pipefail
# D2: only this container mounts the projected credential. No kubeconfig or
# credential is copied into /work (also mounted by the host-reading scanner).
export HOME=/tmp
kubectl config set-cluster local --server="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}" \
  --certificate-authority=/auth/ca.crt --kubeconfig=/tmp/config >/dev/null
kubectl config set-credentials recorder --token="$(cat /auth/token)" --kubeconfig=/tmp/config >/dev/null
kubectl config set-context local --cluster=local --user=recorder --namespace=security-system --kubeconfig=/tmp/config >/dev/null
kubectl config use-context local --kubeconfig=/tmp/config >/dev/null
export KUBECONFIG=/tmp/config
publish() {
  # Merge patches retain no old result key; whole record.json is replaced.
  jq -n --rawfile record /work/record.json '{data:{"record.json":$record}}' > /tmp/patch.json
  kubectl patch configmap "${RECORD_NAME}" --type=merge --patch-file=/tmp/patch.json >/dev/null
}
if [[ "${1:-}" == begin ]]; then
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  version=$(kubectl version -o json | jq -er '.serverVersion.gitVersion | select(type == "string" and length > 0)')
  jq -cn --arg run "${POD_UID}" --arg node "${NODE_NAME}" --arg roles "${NODE_ROLES}" \
    --arg started "${started}" --arg version "${version}" --arg image "${BENCH_IMAGE}" \
    '{schemaVersion:1,runId:$run,tool:"kube-bench",toolVersion:"0.16.0",image:$image,
      benchmarkId:"cis-1.23",node:$node,nodeRoles:($roles|split(",")),kubernetesVersion:$version,
      startedAt:$started,finishedAt:null,status:"error",reason:"scan not completed",totals:null,results:[]}' > /work/meta.json
  cp /work/meta.json /work/record.json
  publish
  exit 0
fi
finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq -c --arg finished "${finished}" '.finishedAt=$finished' /work/meta.json > /tmp/meta.json
# D3: nonzero exit, malformed/truncated JSON, wrong benchmark or empty results
# all overwrite the placeholder with error, and make the Job fail visibly.
if [[ "$(cat /work/exit-code 2>/dev/null || echo missing)" == 0 ]] && \
  jq -ce --argjson meta "$(cat /tmp/meta.json)" -f /scripts/record.jq /work/raw.json > /work/record.json; then
  if [[ $(wc -c < /work/record.json) -lt 900000 ]]; then
    publish
    exit 0
  fi
fi
jq -c '. + {status:"error",reason:"scanner failed, invalid output or record too large",totals:null,results:[]}' \
  /tmp/meta.json > /work/record.json
publish
exit 1
