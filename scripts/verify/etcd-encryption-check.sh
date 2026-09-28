#!/usr/bin/env bash
set -uo pipefail

STRICT=0
if [[ "${1:-}" == --strict ]]; then STRICT=1; shift; fi

ENC_DIR="${ENC_DIR:-/etc/kubernetes/enc}"
CONFIG="${CONFIG:-${ENC_DIR}/encryption-config.yaml}"
MANIFEST="${MANIFEST:-/etc/kubernetes/manifests/kube-apiserver.yaml}"
KEY_FILE="${KEY_FILE:-${ENC_DIR}/encryption-key}"
PASS=0 FAIL=0 SKIP=0

result() {
  local status="$1" name="$2" detail="$3"
  printf '%s %s: %s\n' "$status" "$name" "$detail"
  case "$status" in PASS) PASS=$((PASS + 1));; FAIL) FAIL=$((FAIL + 1));; SKIP) SKIP=$((SKIP + 1));; esac
}

if [[ ! -r "$CONFIG" ]]; then
  result FAIL config "missing/unreadable: $CONFIG"
  echo "Summary: $PASS PASS, $FAIL FAIL, $SKIP SKIP"
  exit 1
fi

# Parse resource/provider order with yq; identity first means bytes are stored in clear.
if ! command -v yq >/dev/null 2>&1; then
  result FAIL providers "yq is required to inspect $CONFIG"
else
  providers="$(sudo yq -r '.resources[] | .resources[] as $r | [$r, (.providers[0] | keys | .[0])] | @tsv' "$CONFIG" 2>/dev/null)"
  if [[ -z "$providers" ]]; then
    result FAIL providers "no protected resources found in $CONFIG"
  else
    while IFS=$'\t' read -r resource first; do
      if [[ "$first" == identity || -z "$first" ]]; then
        result FAIL "provider-$resource" "first provider is ${first:-missing}"
      else
        result PASS "provider-$resource" "first provider is $first"
      fi
    done <<< "$providers"
  fi
fi

details="$(sudo stat -c '%a %U:%G' "$CONFIG" 2>/dev/null)"
if [[ "$details" == "600 root:root" ]]; then result PASS permissions "$CONFIG is $details";
else result FAIL permissions "$CONFIG is $details (expected 600 root:root)"; fi
if [[ -e "$KEY_FILE" ]]; then
  details="$(sudo stat -c '%a %U:%G' "$KEY_FILE" 2>/dev/null)"
  if [[ "$details" == "600 root:root" ]]; then result PASS permissions "$KEY_FILE is $details";
  else result FAIL permissions "$KEY_FILE is $details (expected 600 root:root)"; fi
elif sudo yq -e '[.resources[].providers[] | select(has("aescbc")) | .aescbc.keys[].secret] | length > 0' "$CONFIG" >/dev/null 2>&1; then
  result PASS permissions "legacy layout: key embedded in config (covered by config permissions)"
else
  result FAIL permissions "missing $KEY_FILE and no embedded aescbc key in $CONFIG"
fi

if grep -Fq -- "$CONFIG" "$MANIFEST" 2>/dev/null; then
  result PASS manifest "references $CONFIG"
else
  result FAIL manifest "$MANIFEST does not reference $CONFIG"
fi
if pgrep -af 'kube-apiserver.*--encryption-provider-config=' | grep -Fq -- "$CONFIG"; then
  result PASS process "running apiserver has --encryption-provider-config=$CONFIG"
else
  result FAIL process "running apiserver process flag missing $CONFIG"
fi

KUBECTL="$(command -v kubectl 2>/dev/null || sudo sh -c 'command -v kubectl' 2>/dev/null || true)"
ETCD_POD="etcd-$(hostname)"
ETCDCTL_ARGS=(--endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key)
etcdctl_json() {
  if [[ -n "$KUBECTL" ]]; then
    sudo env KUBECONFIG=/etc/kubernetes/admin.conf "$KUBECTL" -n kube-system exec "$ETCD_POD" -- etcdctl "${ETCDCTL_ARGS[@]}" "$@"
  elif command -v crictl >/dev/null 2>&1; then
    local cid
    cid="$(sudo crictl ps --name etcd -q | head -n1)"
    [[ -n "$cid" ]] && sudo crictl exec "$cid" etcdctl "${ETCDCTL_ARGS[@]}" "$@"
  else return 127; fi
}

if [[ -n "$KUBECTL" ]] && command -v python3 >/dev/null 2>&1; then
  ns="enc-check-$(date +%s)-$$"; secret=raw-check
  cleanup() { sudo env KUBECONFIG=/etc/kubernetes/admin.conf "$KUBECTL" delete namespace "$ns" --wait=false >/dev/null 2>&1 || true; }
  trap cleanup EXIT
  if sudo env KUBECONFIG=/etc/kubernetes/admin.conf "$KUBECTL" create namespace "$ns" >/dev/null 2>&1 && sudo env KUBECONFIG=/etc/kubernetes/admin.conf "$KUBECTL" -n "$ns" create secret generic "$secret" --from-literal=marker="enc-check-${RANDOM}-$(date +%s)" >/dev/null 2>&1; then
    raw_json="$(etcdctl_json get "/registry/secrets/${ns}/${secret}" -w json 2>/dev/null)"
    prefix="$(python3 -c 'import base64,json,sys; v=base64.b64decode(json.load(sys.stdin)["kvs"][0]["value"]); p=(b"k8s:enc:aescbc:",b"k8s:enc:secretbox:",b"k8s:enc:aesgcm:"); print(next(x.decode() for x in p if v.startswith(x)))' <<< "$raw_json" 2>/dev/null)" && result PASS storage "raw etcd value starts with $prefix" || result FAIL storage "raw etcd value is plaintext or has an unsupported prefix"
  else result FAIL storage "could not create probe namespace/Secret"; fi
  cleanup; trap - EXIT
else result FAIL storage "kubectl and Python 3 are required (kubectl exec runs etcdctl in the static pod; crictl is scan fallback)"; fi

# etcdctl's JSON output base64-encodes arbitrary value bytes; decode on the node.
if command -v python3 >/dev/null 2>&1 && { [[ -n "$KUBECTL" ]] || command -v crictl >/dev/null 2>&1; }; then
  count="$(etcdctl_json get /registry/secrets/ --prefix -w json 2>/dev/null | python3 -c 'import base64,json,sys; d=json.load(sys.stdin); print(sum(not base64.b64decode(k["value"]).startswith(b"k8s:enc:") for k in d.get("kvs", [])))' 2>/dev/null)"
  if [[ "$count" =~ ^[0-9]+$ ]]; then
    if [[ "$count" == 0 ]]; then result PASS unencrypted-count "0 Secrets stored without k8s:enc: prefix"; else result FAIL unencrypted-count "$count Secrets stored without k8s:enc: prefix"; fi
  else result FAIL unencrypted-count "could not scan etcd Secrets prefix"; fi
else result FAIL unencrypted-count "Python 3 and kubectl or crictl are required to scan etcd"; fi

if [[ $# -gt 0 ]]; then
  ssh_user="${NODE_SSH_USER:-vagrant}"
  [[ "${PROVIDER:-}" == kakao ]] && ssh_user="${NODE_SSH_USER:-ubuntu}"
  for ip in "$@"; do
    if [[ " $(hostname -I 2>/dev/null) " == *" $ip "* ]]; then
      remote_hash="$(sudo sha256sum "$CONFIG" | awk '{print $1}')"
    elif remote_hash="$(sshpass -p "${NODE_SSH_PASSWORD:-vagrant}" ssh -o StrictHostKeyChecking=no "${ssh_user}@${ip}" "sudo sha256sum '$CONFIG'" 2>/dev/null | awk '{print $1}')" && [[ -n "$remote_hash" ]]; then
      :
    else
      result SKIP "ha-$ip" "unreachable over node SSH; config hash not compared"
      continue
    fi
    local_hash="$(sudo sha256sum "$CONFIG" | awk '{print $1}')"
    if [[ "$remote_hash" == "$local_hash" ]]; then result PASS "ha-$ip" "config SHA-256 matches";
    else result FAIL "ha-$ip" "config SHA-256 differs"; fi
  done
else
  result SKIP ha "no master IPs supplied; pass master IPs as arguments"
fi

printf 'Summary: %d PASS, %d FAIL, %d SKIP\n' "$PASS" "$FAIL" "$SKIP"
(( FAIL == 0 && (STRICT == 0 || SKIP == 0) ))
