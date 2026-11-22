#!/bin/bash
# Compare public leaf certificates outside the pod; never retrieve tls.key/unseal material.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <explicit-kubeconfig>" >&2
  exit 2
fi
KUBE=(kubectl --kubeconfig "$1" --request-timeout=15s -n storage)
work=$(mktemp -d)
forward_pid=
client_pid=
watchdog_pid=
cleanup_attempt() {
  for pid in "$watchdog_pid" "$client_pid" "$forward_pid"; do
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  forward_pid='' client_pid='' watchdog_pid=''
}
# shellcheck disable=SC2329 # Invoked by EXIT trap.
cleanup() { cleanup_attempt; rm -rf "$work"; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
# D-BAO-WINDOW: seven observations, 30s apart, tolerate transient API/connect errors.
# Cost: 180s polling plus bounded request/handshake time; escape hatch: diagnose projection.
for attempt in 0 1 2 3 4 5 6; do
  expected=
  served=
  if expected=$("${KUBE[@]}" get secret openbao-tls -o 'jsonpath={.data.tls\.crt}' |
    openssl base64 -d -A | openssl x509 -noout -fingerprint -sha256 | sed 's/^.*=//'); then
    # Ask kubectl for an available local port; target the Pod even while sealed/NotReady.
    "${KUBE[@]}" port-forward --address 127.0.0.1 pod/openbao-0 :8200 >"$work/forward" 2>&1 &
    forward_pid=$!
    port=
    for _poll in 0 1 2 3 4 5 6 7 8 9; do
      port=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\) -> 8200$/\1/p' "$work/forward")
      [ -z "$port" ] || break
      kill -0 "$forward_pid" 2>/dev/null || break
      sleep 1
    done
    if [ -n "$port" ]; then
      openssl s_client -connect "127.0.0.1:$port" -servername openbao.storage.svc \
        </dev/null >"$work/served" 2>/dev/null &
      client_pid=$!
      (sleep 10; kill "$client_pid" 2>/dev/null || true) >/dev/null 2>&1 &
      watchdog_pid=$!
      if wait "$client_pid"; then
        served=$(openssl x509 -in "$work/served" -noout -fingerprint -sha256 | sed 's/^.*=//') || served=
      fi
      client_pid=
    fi
  fi
  cleanup_attempt
  if [ -n "$expected" ] && [ "$expected" = "$served" ]; then
    echo "PASS: OpenBao served certificate == Secret ($served)"
    exit 0
  fi
  echo "WARN: OpenBao TLS mismatch or transient read/connect error (attempt $((attempt+1))/7)" >&2
  [ "$attempt" -eq 6 ] || sleep 30
done
echo "FAIL: OpenBao served certificate did not match Secret within bounded retry window" >&2
exit 1
