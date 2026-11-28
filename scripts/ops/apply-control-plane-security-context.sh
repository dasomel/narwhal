#!/usr/bin/env bash
# Roll out securityContext hardening (allowPrivilegeEscalation: false, capabilities.drop: [ALL],
# with capabilities.add: [DAC_OVERRIDE] on etcd under D-CP-ETCD-DAC for etcd:etcd 0700 /var/lib/etcd,
# and seccompProfile: RuntimeDefault) to the 4 control-plane static Pods + /etc/kubernetes/patches.
# Dry-run by default; pass --apply to mutate or --rollback BACKUP_DIR to restore one node.
# Local python3/yq v4/kubectl; remote sudo. No remote or cluster writes in dry-run.
set -euo pipefail

KUBECONFIG_PATH=""; TIMEOUT=180; APPLY=0; ROLLBACK=""; IDENTITY=""; KNOWN_HOSTS="$HOME/.ssh/known_hosts"
NODES=(); APPLIED_NODES=(); APPLIED_DIRS=(); SSH_OPTS=()
COMPONENTS=(kube-scheduler kube-controller-manager etcd kube-apiserver)
REVERSE_COMPONENTS=(kube-apiserver etcd kube-controller-manager kube-scheduler)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER="$SCRIPT_DIR/lib/control-plane-security-context.py"
PATCH_DIR="$SCRIPT_DIR/lib/kubeadm-patches"

while (($#)); do
  case "$1" in
    --kubeconfig) KUBECONFIG_PATH="$2"; shift 2 ;;
    --node) NODES+=("$2"); shift 2 ;;
    --local) NODES+=("$2=local"); shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --known-hosts) KNOWN_HOSTS="$2"; shift 2 ;;
    --ssh-opt) SSH_OPTS+=(-o "${2:?--ssh-opt requires an OpenSSH option}"); shift 2 ;;
    --apply) APPLY=1; shift ;;
    --rollback) ROLLBACK="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -r "$KUBECONFIG_PATH" && -r "$HELPER" && -d "$PATCH_DIR" && ${#NODES[@]} -gt 0 && "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo 'usage: --kubeconfig FILE (--node NAME=USER@HOST ... | --local NAME) [--identity KEY] [--known-hosts FILE] [--ssh-opt OPT]... [--timeout SECONDS] [--apply | --rollback BACKUP_DIR]' >&2
  exit 2
}
[[ -z "$ROLLBACK" || ( ${#NODES[@]} == 1 && "$APPLY" == 0 && "$ROLLBACK" =~ ^/var/lib/narwhal/control-plane-security-context/run\.[a-zA-Z0-9_-]+$ ) ]] || exit 2
for mapping in "${NODES[@]}"; do
  [[ "$mapping" =~ ^[a-z0-9.-]+=(local|[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+)$ ]] || exit 2
done

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS")
[[ -z "$IDENTITY" ]] || SSH+=(-i "$IDENTITY")
SSH+=("${SSH_OPTS[@]+"${SSH_OPTS[@]}"}")

kube() { kubectl --kubeconfig "$KUBECONFIG_PATH" --request-timeout=10s "$@"; }
run_root() {
  if [[ "$HOST" == local ]]; then
    if ((EUID == 0)); then "$@"; else sudo -n "$@"; fi
  else
    "${SSH[@]}" "$HOST" sudo -n "$@"
  fi
}
fetch() { run_root cat "$1"; }

remote() {
  run_root bash -s -- "$@" <<'REMOTE'
set -euo pipefail
umask 077
manifest_dir=/etc/kubernetes/manifests
patches_dir=/etc/kubernetes/patches
backup_base=/var/lib/narwhal/control-plane-security-context
tmpdir=/etc/kubernetes/.narwhal-tmp
action="$1"

case "$action" in
  init-backup)
    install -d -o root -g root -m 700 "$backup_base"
    mktemp -d "$backup_base/run.XXXXXXXX"
    exit 0
    ;;
  has-backup)
    [[ -f "$2/$3.yaml" ]]
    exit $?
    ;;
  install-patch)
    install -d -o root -g root -m 700 "$patches_dir" "$tmpdir"
    tmp_dev=$(stat -c %d "$tmpdir")
    pat_dev=$(stat -c %d "$patches_dir")
    [[ -n "$tmp_dev" && "$tmp_dev" == "$pat_dev" ]]
    pfile="$patches_dir/$2"
    ptmp=$(mktemp "$tmpdir/patch.XXXXXXXX")
    trap 'rm -f "$ptmp"' EXIT
    printf '%s' "$3" | base64 -d > "$ptmp"
    chown root:root "$ptmp"
    chmod 600 "$ptmp"
    if [[ -f "$pfile" ]] && ! cmp -s "$ptmp" "$pfile"; then
      echo "refusing: $pfile differs from shipped patch; review scripts/ops/lib/kubeadm-patches/$2" >&2
      return 3
    fi
    if [[ ! -f "$pfile" ]]; then
      mv -f "$ptmp" "$pfile"
    else
      chown root:root "$pfile"
      chmod 600 "$pfile"
      rm -f "$ptmp"
    fi
    exit 0
    ;;
  apply|restore)
    comp="$2"
    manifest="$manifest_dir/$comp.yaml"
    install -d -o root -g root -m 700 "$tmpdir"
    # D8: rename must stay on one filesystem, outside kubelet's watched directory.
    # Cost: explicit st_dev check; escape: abort rather than fall back to copy.
    tmp_dev=$(stat -c %d "$tmpdir")
    man_dev=$(stat -c %d "$manifest_dir")
    [[ -n "$tmp_dev" && "$tmp_dev" == "$man_dev" ]]
    candidate=$(mktemp "$tmpdir/candidate.XXXXXXXX")
    trap 'rm -f "$candidate"' EXIT
    if [[ "$action" == restore ]]; then
      install -o root -g root -m 600 "$3/$comp.yaml" "$candidate"
    else
      bdir="$3"; old_b64="$4"; new_b64="$5"
      printf '%s' "$new_b64" | base64 -d > "$candidate"
      printf '%s' "$old_b64" | base64 -d | cmp -s - "$manifest"
      install -d -o root -g root -m 700 "$bdir"
      install -o root -g root -m 600 "$manifest" "$bdir/$comp.yaml"
    fi
    chown root:root "$candidate"
    chmod 600 "$candidate"
    mv -f "$candidate" "$manifest"
    ;;
  *) exit 2 ;;
esac
REMOTE
}

WORK=$(mktemp -d); chmod 700 "$WORK"
NODE=""; HOST=""; NODE_BACKUP_DIR=""; NODE_CHANGED=()

wait_health() {
  local comp="$1" old_uid="$2" expected="$3" deadline=$((SECONDS + TIMEOUT))
  until kube get --raw /readyz >/dev/null 2>&1 &&
    kube -n kube-system get pod "$comp-$NODE" -o json > "$WORK/pod.json" 2>/dev/null &&
    python3 "$HELPER" health "$WORK/pod.json" "$NODE" "$comp" "$old_uid" "$expected"; do
    if ((SECONDS >= deadline)); then echo "health timeout: $NODE/$comp" >&2; return 1; fi
    sleep 2
  done
}

rollback_command() {
  local mapping="$1" bdir="$2"
  printf 'bash %q --kubeconfig %q' "$0" "$KUBECONFIG_PATH" >&2
  if [[ "${mapping#*=}" == local ]]; then
    printf ' --local %q' "${mapping%%=*}" >&2
  else
    printf ' --node %q --known-hosts %q' "$mapping" "$KNOWN_HOSTS" >&2
  fi
  printf ' --timeout %q' "$TIMEOUT" >&2
  [[ -z "$IDENTITY" ]] || printf ' --identity %q' "$IDENTITY" >&2
  local opt
  for opt in "${SSH_OPTS[@]+"${SSH_OPTS[@]}"}"; do printf ' --ssh-opt %q' "$opt" >&2; done
  printf ' --rollback %q\n' "$bdir" >&2
}

recover() {
  local rc=$? i comp
  trap - EXIT INT TERM
  if ((rc != 0)); then
    if [[ -n "$NODE_BACKUP_DIR" && ${#NODE_CHANGED[@]} -gt 0 ]]; then
      echo "restoring $NODE from $NODE_BACKUP_DIR" >&2
      local failed_restore=0
      for ((i=${#NODE_CHANGED[@]}-1; i>=0; i--)); do
        comp="${NODE_CHANGED[i]}"
        fetch "$NODE_BACKUP_DIR/$comp.yaml" > "$WORK/restore-$comp.yaml" || { failed_restore=1; continue; }
        if remote restore "$comp" "$NODE_BACKUP_DIR" && wait_health "$comp" "" "$WORK/restore-$comp.yaml"; then
          echo "restored and healthy: $NODE/$comp" >&2
        else
          echo "ROLLBACK FAILED: $NODE/$comp" >&2
          failed_restore=1
        fi
      done
      if ((failed_restore)); then rollback_command "$NODE=$HOST" "$NODE_BACKUP_DIR"; fi
    fi
    for ((i=0; i<${#APPLIED_NODES[@]}; i++)); do
      echo "EARLIER NODE STILL APPLIED: ${APPLIED_NODES[i]} backup=${APPLIED_DIRS[i]}" >&2
      rollback_command "${APPLIED_NODES[i]}" "${APPLIED_DIRS[i]}"
    done
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Verify all 4 shipped kubeadm strategic patches exist locally before touching anything.
for comp in "${COMPONENTS[@]}"; do
  [[ -r "$PATCH_DIR/$comp+strategic.yaml" ]] || { echo "missing patch file: $PATCH_DIR/$comp+strategic.yaml" >&2; exit 1; }
done

# Preflight every target node before any mutation.
for mapping in "${NODES[@]}"; do
  NODE=${mapping%%=*}; HOST=${mapping#*=}
  kube get node "$NODE" -o json | python3 -c 'import json,sys; n=json.load(sys.stdin); sys.exit(0 if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels",{}) else 1)'
  if [[ -n "$ROLLBACK" ]]; then
    for comp in "${REVERSE_COMPONENTS[@]}"; do
      if remote has-backup "$ROLLBACK" "$comp"; then
        fetch "$ROLLBACK/$comp.yaml" > "$WORK/restore-$comp.yaml"
        remote restore "$comp" "$ROLLBACK"
        wait_health "$comp" "" "$WORK/restore-$comp.yaml"
        echo "restored and healthy: $NODE/$comp"
      fi
    done
    exit 0
  fi
  mkdir -p "$WORK/$NODE"
  for comp in "${COMPONENTS[@]}"; do
    fetch "/etc/kubernetes/manifests/$comp.yaml" > "$WORK/$NODE/$comp.old"
    python3 "$HELPER" render "$WORK/$NODE/$comp.old" "$WORK/$NODE/$comp.new" "$comp"
    if ((APPLY == 0)); then
      echo "=== dry-run $NODE/$comp ($HOST) ==="
      diff -u "$WORK/$NODE/$comp.old" "$WORK/$NODE/$comp.new" || test "$?" = 1
    fi
  done
done

if ((APPLY == 0)); then
  echo "dry-run complete (no remote or cluster writes)"
  exit 0
fi

for mapping in "${NODES[@]}"; do
  NODE=${mapping%%=*}; HOST=${mapping#*=}; NODE_BACKUP_DIR=""; NODE_CHANGED=()
  # Install kubeadm strategic patches for future kubeadm upgrades.
  for comp in "${COMPONENTS[@]}"; do
    pb64=$(base64 < "$PATCH_DIR/$comp+strategic.yaml" | tr -d '\n')
    remote install-patch "$comp+strategic.yaml" "$pb64"
  done
  for comp in "${COMPONENTS[@]}"; do
    old="$WORK/$NODE/$comp.old"
    new="$WORK/$NODE/$comp.new"
    if ! cmp -s "$old" "$new"; then
      [[ -n "$NODE_BACKUP_DIR" ]] || NODE_BACKUP_DIR=$(remote init-backup)
      old_uid=$(kube -n kube-system get pod "$comp-$NODE" -o jsonpath='{.metadata.uid}')
      NODE_CHANGED+=("$comp")
      remote apply "$comp" "$NODE_BACKUP_DIR" "$(base64 < "$old" | tr -d '\n')" "$(base64 < "$new" | tr -d '\n')"
      fetch "/etc/kubernetes/manifests/$comp.yaml" > "$WORK/$NODE/$comp.written"
      python3 "$HELPER" verify "$old" "$WORK/$NODE/$comp.written" "$comp"
      wait_health "$comp" "$old_uid" "$new"
      echo "healthy $NODE/$comp backup=$NODE_BACKUP_DIR/$comp.yaml"
    else
      wait_health "$comp" "" "$new"
      echo "healthy $NODE/$comp backup=none"
    fi
  done
  if [[ -n "$NODE_BACKUP_DIR" ]]; then
    APPLIED_NODES+=("$mapping"); APPLIED_DIRS+=("$NODE_BACKUP_DIR")
  fi
  NODE_BACKUP_DIR=""; NODE_CHANGED=()
done
