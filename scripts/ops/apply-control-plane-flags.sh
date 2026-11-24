#!/usr/bin/env bash
# Sequential, explicit node mappings: --node narwhal-master-1=vagrant@192.168.56.10
# Requires remote sudo + yq v4, local kubectl + python3; uses the ops SSH pattern.
set -euo pipefail
KUBECONFIG_PATH=""; TIMEOUT=180; DRY_RUN=0; ROLLBACK=""; IDENTITY=""
NODES=()
while (($#)); do
  case "$1" in
    --kubeconfig) KUBECONFIG_PATH="$2"; shift 2 ;;
    --node) NODES+=("$2"); shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --rollback) ROLLBACK="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -r "$KUBECONFIG_PATH" && ${#NODES[@]} -gt 0 && "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo 'usage: --kubeconfig FILE --node NAME=USER@HOST [--node ...] [--identity KEY] [--timeout SECONDS] [--dry-run | --rollback BACKUP]' >&2; exit 2;
}
[[ -z "$ROLLBACK" || ( ${#NODES[@]} == 1 && "$DRY_RUN" == 0 && "$ROLLBACK" =~ ^/var/lib/narwhal/control-plane-flags/manifest\.[a-zA-Z0-9]+$ ) ]] || exit 2
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10)
[[ -z "$IDENTITY" ]] || SSH+=(-i "$IDENTITY")
kube() { kubectl --kubeconfig "$KUBECONFIG_PATH" --request-timeout=10s "$@"; }
remote() {
  "${SSH[@]}" "$HOST" sudo bash -s -- "$1" "${2:-}" <<'REMOTE'
set -euo pipefail
umask 077
manifest=/etc/kubernetes/manifests/kube-controller-manager.yaml
dir=/var/lib/narwhal/control-plane-flags
case "$1" in
  restore)
    # D2: backups never enter the static-pod watched directory. Cost: retained disk
    # files; escape: remove reviewed backups after the live observation window.
    test -f "$2"
    restore=$(mktemp "$dir/restore.XXXXXXXX")
    install -m 600 "$2" "$restore"
    mv -f "$restore" "$manifest"
    ;;
  apply|dry)
    install -d -m 700 "$dir"
    candidate=$(mktemp "$dir/candidate.XXXXXXXX")
    trap 'rm -f "$candidate"' EXIT
    yq '( .spec.containers[] | select(.name == "kube-controller-manager") | .command ) |= (map(select(test("^--terminated-pod-gc-threshold(=|$)") | not)) + ["--terminated-pod-gc-threshold=12500"])' "$manifest" > "$candidate"
    # Refuse malformed/non-kubeadm manifests instead of installing an empty edit.
    yq -e '.spec.containers[] | select(.name == "kube-controller-manager") | .command | any_c(. == "--terminated-pod-gc-threshold=12500")' "$candidate" >/dev/null
    if cmp -s "$manifest" "$candidate"; then echo unchanged; exit 0; fi
    if [[ "$1" == dry ]]; then diff -u "$manifest" "$candidate" || test "$?" = 1; exit 0; fi
    backup=$(mktemp "$dir/manifest.XXXXXXXX")
    install -m 600 "$manifest" "$backup"
    # stdout is the recovery path even if installation fails afterwards.
    echo "$backup"
    chmod 600 "$candidate"
    mv -f "$candidate" "$manifest"
    ;;
esac
REMOTE
}
ready() {
  kube -n kube-system get pod "kube-controller-manager-$NODE" -o json | python3 -c '
import json,sys
p=json.load(sys.stdin)
args=next((c.get("command",[]) for c in p["spec"]["containers"] if c["name"]=="kube-controller-manager"),[])
ready=any(c["type"]=="Ready" and c["status"]=="True" for c in p.get("status",{}).get("conditions",[]))
new=not sys.argv[1] or p["metadata"]["uid"]!=sys.argv[1]
sys.exit(0 if ready and new and "--terminated-pod-gc-threshold=12500" in args else 1)
' "$OLD_UID"
}
BACKUP=""; HOST=""
recover() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$BACKUP" ]]; then
    echo "restoring $NODE from $BACKUP" >&2
    remote restore "$BACKUP" || { echo "ROLLBACK FAILED: $HOST $BACKUP" >&2; exit 1; }
  fi
  exit "$rc"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for mapping in "${NODES[@]}"; do
  [[ "$mapping" =~ ^[a-z0-9.-]+=[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+$ ]] || exit 2
  NODE=${mapping%%=*}; HOST=${mapping#*=}; BACKUP=""
  kube get node "$NODE" -o json | python3 -c 'import json,sys; n=json.load(sys.stdin); sys.exit(0 if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels",{}) else 1)'
  if [[ -n "$ROLLBACK" ]]; then remote restore "$ROLLBACK"; echo "restored $NODE; verify recovery Ready separately"; continue; fi
  if ((DRY_RUN)); then remote dry; continue; fi
  OLD_UID=$(kube -n kube-system get pod "kube-controller-manager-$NODE" -o jsonpath='{.metadata.uid}')
  # D3: retain rollback on any failure after mutation, including API/SSH errors.
  # Cost: requires SSH recovery access; escape: use the printed backup manually.
  if BACKUP=$(remote apply); then :; else exit 1; fi
  if [[ "$BACKUP" == unchanged ]]; then BACKUP=""; OLD_UID=""; fi
  deadline=$((SECONDS + TIMEOUT))
  until ready; do
    if ((SECONDS >= deadline)); then echo "not Ready within ${TIMEOUT}s: $NODE" >&2; exit 1; fi
    sleep 2
  done
  echo "Ready $NODE --terminated-pod-gc-threshold=12500 backup=${BACKUP:-none}"
  BACKUP=""
done
