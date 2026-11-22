#!/bin/bash
# Compare public leaf certificates only; never retrieve tls.key or unseal material.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <explicit-kubeconfig> (requires deployed tls-reloader)" >&2
  exit 2
fi
KUBE=(kubectl --kubeconfig "$1" --request-timeout=15s -n storage)
# D-BAO-WINDOW: allow kubelet projection (~2m) + 30s poll + handshake/scheduling slack.
# Cost: up to 180s per failure; escape hatch: diagnose kubelet before increasing it.
for attempt in 0 1 2 3 4 5 6; do
  expected=$("${KUBE[@]}" get secret openbao-tls -o 'jsonpath={.data.tls\.crt}' |
    openssl base64 -d -A | openssl x509 -noout -fingerprint -sha256 | sed 's/^.*=//') || {
    echo "FAIL: cannot read public Secret certificate" >&2; exit 1;
  }
  served=$("${KUBE[@]}" exec openbao-0 -c tls-reloader -- \
    timeout 10 openssl s_client -connect 127.0.0.1:8200 -servername openbao.storage.svc </dev/null 2>/dev/null |
    openssl x509 -noout -fingerprint -sha256 | sed 's/^.*=//') || {
    echo "FAIL: cannot read served certificate (is tls-reloader deployed?)" >&2; exit 1;
  }
  if [ -n "$expected" ] && [ "$expected" = "$served" ]; then
    echo "PASS: OpenBao served certificate == Secret ($served)"
    exit 0
  fi
  [ "$attempt" -eq 6 ] || sleep 30
done
echo "FAIL: OpenBao served certificate != Secret after 180s" >&2
exit 1
