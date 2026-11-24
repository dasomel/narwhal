#!/usr/bin/env bash
# Local python3/yq v4/kubectl; remote sudo. No remote writes in dry-run.
set -euo pipefail
KUBECONFIG_PATH=""; TIMEOUT=180; DRY_RUN=0; ROLLBACK=""; IDENTITY=""; KNOWN_HOSTS="$HOME/.ssh/known_hosts"
NODES=(); BACKUPS=(); APPLIED=()
HELPER="$(cd "$(dirname "$0")" && pwd)/lib/control-plane-flags.py"
while (($#)); do
  case "$1" in
    --kubeconfig) KUBECONFIG_PATH="$2"; shift 2 ;;
    --node) NODES+=("$2"); shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --known-hosts) KNOWN_HOSTS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --rollback) ROLLBACK="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -r "$KUBECONFIG_PATH" && ${#NODES[@]} -gt 0 && "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo 'usage: --kubeconfig FILE --node NAME=USER@HOST [--identity KEY] [--known-hosts FILE] [--timeout SECONDS] [--dry-run | --rollback BACKUP]' >&2; exit 2;
}
[[ -z "$ROLLBACK" || ( ${#NODES[@]} == 1 && "$DRY_RUN" == 0 && "$ROLLBACK" =~ ^/var/lib/narwhal/control-plane-flags/manifest\.[a-zA-Z0-9_-]+$ ) ]] || exit 2
for mapping in "${NODES[@]}"; do
  [[ "$mapping" =~ ^[a-z0-9.-]+=[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+$ ]] || exit 2
done
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS")
[[ -z "$IDENTITY" ]] || SSH+=(-i "$IDENTITY")
kube() { kubectl --kubeconfig "$KUBECONFIG_PATH" --request-timeout=10s "$@"; }
WORK=$(mktemp -d); chmod 700 "$WORK"
MANIFEST=/etc/kubernetes/manifests/kube-controller-manager.yaml
fetch() { "${SSH[@]}" "$HOST" sudo cat "${1:-$MANIFEST}"; }
remote() {
  "${SSH[@]}" "$HOST" sudo bash -s -- "$1" "${2:-}" "${3:-}" <<'REMOTE'
set -euo pipefail
umask 077
manifest=/etc/kubernetes/manifests/kube-controller-manager.yaml
dir=/var/lib/narwhal/control-plane-flags
tmpdir=/etc/kubernetes/.narwhal-tmp
install -d -m 700 "$tmpdir"
# D8: rename must stay on one filesystem, outside kubelet's watched directory.
# Cost: explicit st_dev check; escape: abort rather than fall back to copy.
[[ $(stat -c %d "$tmpdir") == "$(stat -c %d "$(dirname "$manifest")")" ]]
candidate=$(mktemp "$tmpdir/candidate.XXXXXXXX")
trap 'rm -f "$candidate"' EXIT
case "$1" in
  restore) install -m 600 "$2" "$candidate" ;;
  apply)
    printf '%s' "$3" | base64 -d > "$candidate"
    printf '%s' "$2" | base64 -d | cmp -s - "$manifest"
    install -d -m 700 "$dir"
    backup=$(mktemp "$dir/manifest.XXXXXXXX")
    install -m 600 "$manifest" "$backup"
    echo "$backup"
    ;;
esac
chmod 600 "$candidate"
mv -f "$candidate" "$manifest"
REMOTE
}
wait_health() {
  local deadline=$((SECONDS + TIMEOUT))
  until kube -n kube-system get pods -l component=kube-controller-manager -o json > "$WORK/pods.json" &&
    kube -n kube-system get lease kube-controller-manager -o json > "$WORK/lease.json" &&
    python3 "$HELPER" health "$WORK/pods.json" "$WORK/lease.json" "$NODE" "$OLD_UID" "$WORK/expected" "$BASELINE"; do
    if ((SECONDS >= deadline)); then echo "health timeout: $NODE (Ready/command/Lease)" >&2; return 1; fi
    sleep 2
  done
}
snapshot() {
  OLD_UID=$(kube -n kube-system get pod "kube-controller-manager-$NODE" -o jsonpath='{.metadata.uid}')
  BASELINE=$(kube -n kube-system get lease kube-controller-manager -o jsonpath='{.spec.renewTime}')
}
BACKUP=""; HOST=""; NODE=""
rollback_command() {
  printf 'bash %q --kubeconfig %q --node %q --known-hosts %q --timeout %q' "$0" "$KUBECONFIG_PATH" "$1" "$KNOWN_HOSTS" "$TIMEOUT" >&2
  [[ -z "$IDENTITY" ]] || printf ' --identity %q' "$IDENTITY" >&2
  printf ' --rollback %q\n' "$2" >&2
}
recover() {
  local rc=$? i
  trap - EXIT INT TERM
  if ((rc != 0)); then
    if [[ -n "$BACKUP" ]]; then
      echo "restoring $NODE from $BACKUP" >&2
      fetch "$BACKUP" > "$WORK/expected" || true
      snapshot || true
      OLD_UID=""
      if remote restore "$BACKUP" && wait_health; then
        echo "restored and healthy: $NODE" >&2
      else
        echo "ROLLBACK FAILED: $NODE" >&2
        rollback_command "$NODE=$HOST" "$BACKUP"
      fi
    fi
    # D9: keep every recovery reference until total success; cost: mixed state on
    # later failure. Escape: print exact rollback commands and exit non-zero.
    for ((i=0; i<${#APPLIED[@]}; i++)); do
      echo "EARLIER NODE STILL APPLIED: ${APPLIED[i]} backup=${BACKUPS[i]}" >&2
      rollback_command "${APPLIED[i]}" "${BACKUPS[i]}"
    done
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for mapping in "${NODES[@]}"; do
  NODE=${mapping%%=*}; HOST=${mapping#*=}; BACKUP=""
  kube get node "$NODE" -o json | python3 -c 'import json,sys; n=json.load(sys.stdin); sys.exit(0 if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels",{}) else 1)'
  if [[ -n "$ROLLBACK" ]]; then
    fetch "$ROLLBACK" > "$WORK/expected"
    snapshot
    remote restore "$ROLLBACK"
    OLD_UID=""
    wait_health
    echo "restored and healthy: $NODE"
    continue
  fi
  fetch > "$WORK/old"
  python3 "$HELPER" render "$WORK/old" "$WORK/expected"
  if ((DRY_RUN)); then diff -u "$WORK/old" "$WORK/expected" || test "$?" = 1; continue; fi
  snapshot
  if ! cmp -s "$WORK/old" "$WORK/expected"; then
    if BACKUP=$(remote apply "$(base64 < "$WORK/old" | tr -d '\n')" "$(base64 < "$WORK/expected" | tr -d '\n')"); then :; else exit 1; fi
    fetch > "$WORK/written"
    python3 "$HELPER" verify "$WORK/old" "$WORK/written"
  else
    OLD_UID=""
  fi
  wait_health
  echo "healthy $NODE backup=${BACKUP:-none}"
  if [[ -n "$BACKUP" ]]; then APPLIED+=("$mapping"); BACKUPS+=("$BACKUP"); fi
  # The reference remains in BACKUPS for later failures and manual recovery.
  BACKUP=""
done
