#!/usr/bin/env bash
# Roll out EventRateLimit AdmissionConfiguration + kube-apiserver flags sequentially; dry-run by default.
# Local python3/yq v4/kubectl; remote sudo. No remote or cluster writes in dry-run.
set -euo pipefail

KUBECONFIG_PATH=""; TIMEOUT=180; APPLY=0; ROLLBACK=""; IDENTITY=""; KNOWN_HOSTS="$HOME/.ssh/known_hosts"
NODES=(); BACKUPS=(); APPLIED=(); SSH_OPTS=()
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER="$SCRIPT_DIR/lib/apiserver-admission.py"
ADMISSION_CFG="$SCRIPT_DIR/lib/admission-config.yaml"

while (($#)); do
  case "$1" in
    --kubeconfig) KUBECONFIG_PATH="$2"; shift 2 ;;
    --node) NODES+=("$2"); shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --known-hosts) KNOWN_HOSTS="$2"; shift 2 ;;
    --ssh-opt) SSH_OPTS+=(-o "${2:?--ssh-opt requires an OpenSSH option}"); shift 2 ;;
    --apply) APPLY=1; shift ;;
    --rollback) ROLLBACK="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -r "$KUBECONFIG_PATH" && -r "$ADMISSION_CFG" && ${#NODES[@]} -gt 0 && "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo 'usage: --kubeconfig FILE --node NAME=USER@HOST [--identity KEY] [--known-hosts FILE] [--ssh-opt OPT] [--timeout SECONDS] [--apply | --rollback BACKUP]' >&2
  exit 2
}
[[ -z "$ROLLBACK" || ( ${#NODES[@]} == 1 && "$APPLY" == 0 && "$ROLLBACK" =~ ^/var/lib/narwhal/apiserver-admission/manifest\.[a-zA-Z0-9_-]+$ ) ]] || exit 2
for mapping in "${NODES[@]}"; do
  [[ "$mapping" =~ ^[a-z0-9.-]+=[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+$ ]] || exit 2
done

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS")
[[ -z "$IDENTITY" ]] || SSH+=(-i "$IDENTITY")
SSH+=("${SSH_OPTS[@]+"${SSH_OPTS[@]}"}")

kube() { kubectl --kubeconfig "$KUBECONFIG_PATH" --request-timeout=10s "$@"; }

WORK=$(mktemp -d); chmod 700 "$WORK"
MANIFEST=/etc/kubernetes/manifests/kube-apiserver.yaml
fetch() { "${SSH[@]}" "$HOST" sudo cat "${1:-$MANIFEST}"; }

remote() {
  "${SSH[@]}" "$HOST" sudo bash -s -- "$1" "${2:-}" "${3:-}" "${4:-}" <<'REMOTE'
set -euo pipefail
umask 077
manifest=/etc/kubernetes/manifests/kube-apiserver.yaml
admission_dir=/etc/kubernetes/admission
admission_file=/etc/kubernetes/admission/admission-config.yaml
dir=/var/lib/narwhal/apiserver-admission
tmpdir=/etc/kubernetes/.narwhal-tmp
install -d -o root -g root -m 700 "$tmpdir"
# D8: rename must stay on one filesystem, outside kubelet's watched directory.
# Cost: explicit st_dev check; escape: abort rather than fall back to copy.
tmp_dev=$(stat -c %d "$tmpdir")
man_dev=$(stat -c %d "$(dirname "$manifest")")
[[ -n "$tmp_dev" && "$tmp_dev" == "$man_dev" ]]
candidate=$(mktemp "$tmpdir/candidate.XXXXXXXX")
adm_tmp=""
trap 'rm -f "$candidate" "$adm_tmp"' EXIT

install_admission() {
  local cfg_b64="$1"
  install -d -o root -g root -m 700 "$admission_dir"
  adm_tmp=$(mktemp "$tmpdir/admission.XXXXXXXX")
  printf '%s' "$cfg_b64" | base64 -d > "$adm_tmp"
  chown root:root "$adm_tmp"
  chmod 600 "$adm_tmp"
  if [[ ! -f "$admission_file" ]] || ! cmp -s "$adm_tmp" "$admission_file"; then
    mv -f "$adm_tmp" "$admission_file"
    adm_tmp=""
  else
    chown root:root "$admission_file"
    chmod 600 "$admission_file"
    rm -f "$adm_tmp"
    adm_tmp=""
  fi
}

case "$1" in
  install-config)
    install_admission "$2"
    exit 0
    ;;
  restore)
    install -m 600 "$2" "$candidate"
    ;;
  apply)
    install_admission "$4"
    printf '%s' "$3" | base64 -d > "$candidate"
    printf '%s' "$2" | base64 -d | cmp -s - "$manifest"
    install -d -o root -g root -m 700 "$dir"
    backup=$(mktemp "$dir/manifest.XXXXXXXX")
    install -o root -g root -m 600 "$manifest" "$backup"
    echo "$backup"
    ;;
  *)
    exit 2
    ;;
esac
chown root:root "$candidate"
chmod 600 "$candidate"
mv -f "$candidate" "$manifest"
REMOTE
}

wait_health() {
  local deadline=$((SECONDS + TIMEOUT))
  until kube get --raw /readyz >/dev/null 2>&1 &&
    kube -n kube-system get pod "kube-apiserver-$NODE" -o json > "$WORK/pod.json" &&
    python3 "$HELPER" health "$WORK/pod.json" "$NODE" "$OLD_UID" "$WORK/expected"; do
    if ((SECONDS >= deadline)); then echo "health timeout: $NODE (Ready/command)" >&2; return 1; fi
    sleep 2
  done
}

snapshot() {
  OLD_UID=$(kube -n kube-system get pod "kube-apiserver-$NODE" -o jsonpath='{.metadata.uid}')
}

BACKUP=""; HOST=""; NODE=""; OLD_UID=""
rollback_command() {
  printf 'bash %q --kubeconfig %q --node %q --known-hosts %q --timeout %q' "$0" "$KUBECONFIG_PATH" "$1" "$KNOWN_HOSTS" "$TIMEOUT" >&2
  [[ -z "$IDENTITY" ]] || printf ' --identity %q' "$IDENTITY" >&2
  local opt
  for opt in "${SSH_OPTS[@]+"${SSH_OPTS[@]}"}"; do printf ' --ssh-opt %q' "$opt" >&2; done
  printf ' --rollback %q\n' "$2" >&2
}

recover() {
  local rc=$? i
  trap - EXIT INT TERM
  if ((rc != 0)); then
    if [[ -n "$BACKUP" ]]; then
      echo "restoring $NODE from $BACKUP" >&2
      fetch "$BACKUP" > "$WORK/expected" || true
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

# Preflight kubeadm-config ClusterConfiguration unless rolling back a single node manifest.
if [[ -z "$ROLLBACK" ]]; then
  kube -n kube-system get cm kubeadm-config -o json > "$WORK/kubeadm.json"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["ClusterConfiguration"], end="")' "$WORK/kubeadm.json" > "$WORK/cluster.yaml"
  python3 "$HELPER" render-config "$WORK/cluster.yaml" "$WORK/cluster-new.yaml"
fi

ADMISSION_CFG_B64=$(base64 < "$ADMISSION_CFG" | tr -d '\n')

for mapping in "${NODES[@]}"; do
  NODE=${mapping%%=*}; HOST=${mapping#*=}; BACKUP=""
  kube get node "$NODE" -o json | python3 -c 'import json,sys; n=json.load(sys.stdin); sys.exit(0 if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels",{}) else 1)'
  if [[ -n "$ROLLBACK" ]]; then
    fetch "$ROLLBACK" > "$WORK/expected"
    remote restore "$ROLLBACK"
    OLD_UID=""
    wait_health
    echo "restored and healthy: $NODE"
    continue
  fi
  fetch > "$WORK/old"
  python3 "$HELPER" render "$WORK/old" "$WORK/expected"
  if ((APPLY == 0)); then
    echo "=== dry-run $NODE ($HOST) ==="
    diff -u "$WORK/old" "$WORK/expected" || test "$?" = 1
    continue
  fi
  if ! cmp -s "$WORK/old" "$WORK/expected"; then
    snapshot
    if BACKUP=$(remote apply "$(base64 < "$WORK/old" | tr -d '\n')" "$(base64 < "$WORK/expected" | tr -d '\n')" "$ADMISSION_CFG_B64"); then :; else exit 1; fi
    fetch > "$WORK/written"
    python3 "$HELPER" verify "$WORK/old" "$WORK/written"
  else
    remote install-config "$ADMISSION_CFG_B64"
    OLD_UID=""
  fi
  wait_health
  echo "healthy $NODE backup=${BACKUP:-none}"
  if [[ -n "$BACKUP" ]]; then APPLIED+=("$mapping"); BACKUPS+=("$BACKUP"); fi
  BACKUP=""
done

if [[ -n "$ROLLBACK" ]]; then
  exit 0
fi

if ((APPLY == 0)); then
  echo "=== dry-run kube-system/kubeadm-config ==="
  diff -u "$WORK/cluster.yaml" "$WORK/cluster-new.yaml" || test "$?" = 1
  exit 0
fi

# D-ERL-PERSIST: update kubeadm-config only after every control-plane static pod is
# Ready with the new command and mounts. Cost: an interrupted multi-node rollout leaves
# applied nodes ahead of kubeadm-config until re-run; escape: re-run --apply.
if ! cmp -s "$WORK/cluster.yaml" "$WORK/cluster-new.yaml"; then
  python3 - "$WORK/kubeadm.json" "$WORK/cluster-new.yaml" "$WORK/cluster-patch.json" <<'PYPATCH'
import json, pathlib, sys
cm = json.loads(pathlib.Path(sys.argv[1]).read_text())
cfg = pathlib.Path(sys.argv[2]).read_text()
patch = [
    {"op": "test", "path": "/metadata/resourceVersion", "value": cm["metadata"]["resourceVersion"]},
    {"op": "replace", "path": "/data/ClusterConfiguration", "value": cfg},
]
pathlib.Path(sys.argv[3]).write_text(json.dumps(patch))
PYPATCH
  kube -n kube-system patch cm kubeadm-config --type=json --patch-file "$WORK/cluster-patch.json" -o json > "$WORK/kubeadm-after.json"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["ClusterConfiguration"], end="")' "$WORK/kubeadm-after.json" > "$WORK/cluster-patched.yaml"
  python3 "$HELPER" verify-config "$WORK/cluster.yaml" "$WORK/cluster-patched.yaml"
  echo "kubeadm-config: patched ClusterConfiguration"
else
  echo "kubeadm-config: already up to date"
fi
