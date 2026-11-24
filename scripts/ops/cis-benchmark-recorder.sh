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
  jq -n --rawfile record /tmp/record.json '{data:{"record.json":$record}}' > /tmp/patch.json
  kubectl patch configmap "${RECORD_NAME}" --type=merge --patch-file=/tmp/patch.json >/dev/null
}
if [[ "${1:-}" == begin ]]; then
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  version=$(kubectl version -o json | jq -er '.serverVersion.gitVersion | select(type == "string" and length > 0)')
  jq -cn --arg run "${POD_UID}" --arg node "${NODE_NAME}" --arg roles "${NODE_ROLES}" \
    --arg started "${started}" --arg version "${version}" --arg image "${BENCH_IMAGE}" \
    '{schemaVersion:1,runId:$run,tool:"kube-bench",toolVersion:"0.16.0",image:$image,
      benchmarkId:"cis-1.23",node:$node,nodeRoles:($roles|split(",")),kubernetesVersion:$version,
      startedAt:$started,finishedAt:null,status:"error",reason:"scan not completed",totals:null,results:[]}' > /tmp/run-meta.json
  cp /tmp/run-meta.json /tmp/record.json
  publish
  exit 0
fi
finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq -c --arg finished "${finished}" --arg node "${NODE_NAME}" \
  '.finishedAt=$finished | .node=$node' /tmp/run-meta.json > /tmp/meta.json
# D4: metadata and published record stay on the recorder-only tmp volume.
# Cost: shared init state; escape hatch: fail the run if protected metadata is lost.
# D3: nonzero exit, malformed/truncated JSON, wrong benchmark or empty results
# all overwrite the placeholder with error, and make the Job fail visibly.
# kube-bench prints its version with a leading v (measured live 2026-11-24: "v0.16.0").
if [[ "$(cat /work/version 2>/dev/null || echo missing)" != v0.16.0 ]]; then
  jq -c '. + {status:"error",reason:"scanner version mismatch (expected v0.16.0)",totals:null,results:[]}' \
    /tmp/meta.json > /tmp/record.json
  publish
  exit 1
fi
if jq -e --arg benchmark "$(jq -r .benchmarkId /tmp/meta.json)" '
  if (.Controls | type) == "array" and (.Controls | length) > 0
  then any(.Controls[]; .version != $benchmark) else false end' /work/raw.json >/dev/null 2>&1; then
  jq -c '. + {status:"error",reason:"scanner benchmark mismatch (expected cis-1.23)",totals:null,results:[]}' \
    /tmp/meta.json > /tmp/record.json
  publish
  exit 1
fi
if [[ "$(cat /work/exit-code 2>/dev/null || echo missing)" == 0 ]] && \
  jq -ce --argjson meta "$(cat /tmp/meta.json)" -f /scripts/record.jq /work/raw.json > /tmp/record.json; then
  if [[ $(wc -c < /tmp/record.json) -lt 900000 ]]; then
    publish
    exit 0
  fi
fi
jq -c '. + {status:"error",reason:"scanner failed, invalid output or record too large",totals:null,results:[]}' \
  /tmp/meta.json > /tmp/record.json
publish
exit 1
