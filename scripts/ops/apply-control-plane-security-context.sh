#!/usr/bin/env bash
# Roll out securityContext hardening (allowPrivilegeEscalation: false, capabilities.drop: [ALL],
# and seccompProfile: RuntimeDefault) to control-plane static Pods (kube-scheduler,
# kube-controller-manager, kube-apiserver) + /etc/kubernetes/patches.
# D-CP-ETCD-DEFER: etcd is intentionally excluded and stays unmodified.
# Dry-run by default; pass --apply to mutate or --rollback BACKUP_DIR to restore one node.
# Node-side health check uses crictl or ctr + local HTTPS health endpoints (no API dependency);
# API mirror-pod check runs only as an additional non-blocking report.
# Local python3/yq v4/kubectl; remote sudo/crictl-or-ctr/curl. No remote or cluster writes in dry-run.
set -euo pipefail

KUBECONFIG_PATH=""; TIMEOUT=180; APPLY=0; ROLLBACK=""; IDENTITY=""; KNOWN_HOSTS="$HOME/.ssh/known_hosts"
NODES=(); APPLIED_NODES=(); APPLIED_DIRS=(); SSH_OPTS=()
ALL_COMPONENTS=(kube-scheduler kube-controller-manager kube-apiserver)
SELECTED_COMPONENTS=()
COMPONENTS=()
REVERSE_COMPONENTS=()
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER="$SCRIPT_DIR/lib/control-plane-security-context.py"
PATCH_DIR="$SCRIPT_DIR/lib/kubeadm-patches"

while (($#)); do
  case "$1" in
    --kubeconfig) KUBECONFIG_PATH="$2"; shift 2 ;;
    --node) NODES+=("$2"); shift 2 ;;
    --local) NODES+=("$2=local"); shift 2 ;;
    --component) SELECTED_COMPONENTS+=("${2:?--component requires a component name}"); shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --known-hosts) KNOWN_HOSTS="$2"; shift 2 ;;
    --ssh-opt) SSH_OPTS+=(-o "${2:?--ssh-opt requires an OpenSSH option}"); shift 2 ;;
    --apply) APPLY=1; shift ;;
    --rollback) ROLLBACK="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

usage() {
  echo 'usage: --kubeconfig FILE (--node NAME=USER@HOST ... | --local NAME) [--component kube-scheduler|kube-controller-manager|kube-apiserver]... [--identity KEY] [--known-hosts FILE] [--ssh-opt OPT]... [--timeout SECONDS] [--apply | --rollback BACKUP_DIR]' >&2
  exit 2
}

[[ -r "$KUBECONFIG_PATH" && -r "$HELPER" && -d "$PATCH_DIR" && ${#NODES[@]} -gt 0 && "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || usage
[[ -z "$ROLLBACK" || ( ${#NODES[@]} == 1 && "$APPLY" == 0 && "$ROLLBACK" =~ ^/var/lib/narwhal/control-plane-security-context/run\.[a-zA-Z0-9_-]+$ ) ]] || exit 2
for mapping in "${NODES[@]}"; do
  [[ "$mapping" =~ ^[a-z0-9.-]+=(local|[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+)$ ]] || exit 2
done

if ((${#SELECTED_COMPONENTS[@]} == 0)); then
  COMPONENTS=("${ALL_COMPONENTS[@]}")
else
  for sel in "${SELECTED_COMPONENTS[@]}"; do
    case "$sel" in
      kube-scheduler|kube-controller-manager|kube-apiserver) ;;
      *)
        echo "unsupported --component: $sel (allowed: kube-scheduler, kube-controller-manager, kube-apiserver)" >&2
        exit 2
        ;;
    esac
    dup=0
    for existing in "${COMPONENTS[@]+"${COMPONENTS[@]}"}"; do
      if [[ "$existing" == "$sel" ]]; then dup=1; break; fi
    done
    ((dup)) || COMPONENTS+=("$sel")
  done
fi
for ((i=${#COMPONENTS[@]}-1; i>=0; i--)); do
  REVERSE_COMPONENTS+=("${COMPONENTS[i]}")
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

find_crictl() {
  local bin=""
  bin=$(command -v crictl 2>/dev/null || true)
  if [[ -z "$bin" ]]; then
    local cand
    for cand in /usr/local/bin/crictl /usr/bin/crictl; do
      if [[ -x "$cand" ]]; then bin="$cand"; break; fi
    done
  fi
  [[ -z "$bin" || ! -x "$bin" ]] || { printf '%s\n' "$bin"; return 0; }
  return 1
}

find_ctr() {
  command -v ctr 2>/dev/null || { [[ -x /usr/bin/ctr ]] && printf '%s\n' /usr/bin/ctr; }
}

ctr_running_cid() {
  local comp="$1" ctr_bin cid tasks
  ctr_bin=$(find_ctr) || return 1
  tasks=$("$ctr_bin" -n k8s.io task ls)
  while IFS= read -r cid; do
    [[ -n "$cid" ]] || continue
    if awk -v id="$cid" '$1 == id && $3 == "RUNNING" {found=1} END {exit !found}' <<< "$tasks"; then
      printf '%s\n' "$cid"
      return 0
    fi
  done < <("$ctr_bin" -n k8s.io containers ls -q "labels.\"io.kubernetes.container.name\"==$comp")
  return 1
}

health_url_for() {
  case "$1" in
    kube-apiserver) printf '%s\n' 'https://127.0.0.1:6443/readyz' ;;
    kube-controller-manager) printf '%s\n' 'https://127.0.0.1:10257/healthz' ;;
    kube-scheduler) printf '%s\n' 'https://127.0.0.1:10259/healthz' ;;
    *) echo "unsupported component for health endpoint: $1" >&2; return 2 ;;
  esac
}

case "$action" in
  check-prereqs)
    find_crictl >/dev/null || find_ctr >/dev/null || { echo "neither crictl nor ctr found on target node" >&2; exit 1; }
    command -v curl >/dev/null 2>&1 || { echo "curl not found on target node" >&2; exit 1; }
    exit 0
    ;;
  init-backup)
    install -d -o root -g root -m 700 "$backup_base"
    mktemp -d "$backup_base/run.XXXXXXXX"
    exit 0
    ;;
  has-backup)
    [[ -f "$2/$3.yaml" ]]
    exit $?
    ;;
  current-cid)
    comp="$2"
    if crictl_bin=$(find_crictl); then
      cids=$("$crictl_bin" ps --name "^${comp}$" --state running -q 2>/dev/null || true)
      printf '%s\n' "${cids%%$'\n'*}"
    else
      ctr_running_cid "$comp" || true
    fi
    exit 0
    ;;
  node-health)
    comp="$2"
    url=$(health_url_for "$comp")
    curl -skf --connect-timeout 5 --max-time 10 "$url" >/dev/null 2>&1 || exit 1
    if crictl_bin=$(find_crictl); then
      cids=$("$crictl_bin" ps --name "^${comp}$" --state running -q 2>/dev/null || true)
      cid="${cids%%$'\n'*}"
      [[ -n "$cid" ]] || exit 1
      "$crictl_bin" inspect "$cid"
    else
      ctr_bin=$(find_ctr) || exit 1
      cid=$(ctr_running_cid "$comp") || exit 1
      "$ctr_bin" -n k8s.io containers info "$cid" | python3 -c 'import json,os,sys; d=json.load(sys.stdin); d["_ctrId"]=sys.argv[1]; d["_manifestMtimeNs"]=os.stat(sys.argv[2]).st_mtime_ns; print(json.dumps(d))' "$cid" "/etc/kubernetes/manifests/$comp.yaml"
    fi
    exit 0
    ;;
  save-diagnostics)
    comp="$2"
    bdir="$3"
    diag_dir="$bdir/diagnostics/$comp"
    install -d -o root -g root -m 700 "$diag_dir"
    if ! crictl_bin=$(find_crictl); then
      ctr_bin=$(find_ctr) || { echo "neither crictl nor ctr found" >&2; exit 1; }
      "$ctr_bin" -n k8s.io containers ls -q "labels.\"io.kubernetes.container.name\"==$comp" > "$diag_dir/containers.txt" 2>&1 || true
      while IFS= read -r cid; do
        [[ -n "$cid" ]] || continue
        "$ctr_bin" -n k8s.io containers info "$cid" > "$diag_dir/$cid-info.json" 2>&1 || true
      done < "$diag_dir/containers.txt"
      for logfile in /var/log/pods/kube-system_${comp}-"$(hostname)"_*/"$comp"/*.log; do
        [[ -f "$logfile" ]] || continue
        tail -n 300 "$logfile" >> "$diag_dir/container.log" 2>&1 || true
      done
      chmod 600 "$diag_dir"/* 2>/dev/null || true
      printf '%s\n' "$diag_dir"
      exit 0
    fi
    "$crictl_bin" ps -a --name "^${comp}$" > "$diag_dir/ps-a.txt" 2>&1 || true
    all_cids=$("$crictl_bin" ps -a --name "^${comp}$" -q 2>/dev/null || true)
    cur_cid=""; prev_cid=""
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      if [[ -z "$cur_cid" ]]; then
        cur_cid="$line"
      elif [[ -z "$prev_cid" ]]; then
        prev_cid="$line"
        break
      fi
    done <<< "$all_cids"
    if [[ -n "$cur_cid" ]]; then
      "$crictl_bin" logs --tail 300 "$cur_cid" > "$diag_dir/current.log" 2>&1 || true
      "$crictl_bin" inspect "$cur_cid" > "$diag_dir/current-inspect.json" 2>&1 || true
    fi
    if [[ -n "$prev_cid" ]]; then
      "$crictl_bin" logs --tail 300 "$prev_cid" > "$diag_dir/previous.log" 2>&1 || true
      "$crictl_bin" inspect "$prev_cid" > "$diag_dir/previous-inspect.json" 2>&1 || true
    fi
    chmod 600 "$diag_dir"/* 2>/dev/null || true
    printf '%s\n' "$diag_dir"
    exit 0
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
      exit 3
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
  local comp="$1" old_cid="$2" expected="$3" deadline=$((SECONDS + TIMEOUT))
  until remote node-health "$comp" > "$WORK/inspect.json" 2>/dev/null &&
    python3 "$HELPER" auto-health "$WORK/inspect.json" "$comp" "$old_cid" "$expected" 2>/dev/null; do
    if ((SECONDS >= deadline)); then
      echo "health timeout: $NODE/$comp (node-side crictl + local health endpoint)" >&2
      return 1
    fi
    sleep 2
  done
}

report_mirror_pod() {
  local comp="$1" old_uid="$2" expected="$3"
  if kube get --raw /readyz >/dev/null 2>&1 &&
    kube -n kube-system get pod "$comp-$NODE" -o json > "$WORK/pod.json" 2>/dev/null &&
    python3 "$HELPER" health "$WORK/pod.json" "$NODE" "$comp" "$old_uid" "$expected" >/dev/null 2>&1; then
    echo "mirror-pod report (non-blocking): ok $NODE/$comp"
  else
    echo "mirror-pod report (non-blocking): pending or API unreachable for $NODE/$comp" >&2
  fi
}

rollback_command() {
  local mapping="$1" bdir="$2"
  printf 'bash %q --kubeconfig %q' "$0" "$KUBECONFIG_PATH" >&2
  if [[ "${mapping#*=}" == local ]]; then
    printf ' --local %q' "${mapping%%=*}" >&2
  else
    printf ' --node %q --known-hosts %q' "$mapping" "$KNOWN_HOSTS" >&2
  fi
  local sel
  for sel in "${SELECTED_COMPONENTS[@]+"${SELECTED_COMPONENTS[@]}"}"; do
    printf ' --component %q' "$sel" >&2
  done
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
      local failing_comp="${NODE_CHANGED[${#NODE_CHANGED[@]}-1]}"
      local diag_path=""
      if diag_path=$(remote save-diagnostics "$failing_comp" "$NODE_BACKUP_DIR"); then
        echo "saved diagnostics before restore: $NODE/$failing_comp path=$diag_path" >&2
      else
        echo "WARNING: failed to save diagnostics for $NODE/$failing_comp into $NODE_BACKUP_DIR" >&2
      fi
      echo "restoring $NODE from $NODE_BACKUP_DIR" >&2
      local failed_restore=0 pre_restore_cid=""
      for ((i=${#NODE_CHANGED[@]}-1; i>=0; i--)); do
        comp="${NODE_CHANGED[i]}"
        fetch "$NODE_BACKUP_DIR/$comp.yaml" > "$WORK/restore-$comp.yaml" || { failed_restore=1; continue; }
        pre_restore_cid=$(remote current-cid "$comp" 2>/dev/null || true)
        if remote restore "$comp" "$NODE_BACKUP_DIR" && wait_health "$comp" "$pre_restore_cid" "$WORK/restore-$comp.yaml"; then
          echo "restored and healthy: $NODE/$comp" >&2
          report_mirror_pod "$comp" "" "$WORK/restore-$comp.yaml"
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

# Verify all selected shipped kubeadm strategic patches exist locally before touching anything.
for comp in "${COMPONENTS[@]}"; do
  [[ -r "$PATCH_DIR/$comp+strategic.yaml" ]] || { echo "missing patch file: $PATCH_DIR/$comp+strategic.yaml" >&2; exit 1; }
done

# Preflight every target node before any mutation.
for mapping in "${NODES[@]}"; do
  NODE=${mapping%%=*}; HOST=${mapping#*=}
  if [[ -n "$ROLLBACK" ]]; then
    remote check-prereqs
    for comp in "${REVERSE_COMPONENTS[@]}"; do
      if remote has-backup "$ROLLBACK" "$comp"; then
        fetch "$ROLLBACK/$comp.yaml" > "$WORK/restore-$comp.yaml"
        pre_restore_cid=$(remote current-cid "$comp" 2>/dev/null || true)
        remote restore "$comp" "$ROLLBACK"
        wait_health "$comp" "$pre_restore_cid" "$WORK/restore-$comp.yaml"
        report_mirror_pod "$comp" "" "$WORK/restore-$comp.yaml"
        echo "restored and healthy: $NODE/$comp"
      fi
    done
    exit 0
  fi
  kube get node "$NODE" -o json | python3 -c 'import json,sys; n=json.load(sys.stdin); sys.exit(0 if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels",{}) else 1)'
  remote check-prereqs
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
      old_cid=$(remote current-cid "$comp")
      old_uid=$(kube -n kube-system get pod "$comp-$NODE" -o jsonpath='{.metadata.uid}' 2>/dev/null || true)
      NODE_CHANGED+=("$comp")
      remote apply "$comp" "$NODE_BACKUP_DIR" "$(base64 < "$old" | tr -d '\n')" "$(base64 < "$new" | tr -d '\n')"
      fetch "/etc/kubernetes/manifests/$comp.yaml" > "$WORK/$NODE/$comp.written"
      python3 "$HELPER" verify "$old" "$WORK/$NODE/$comp.written" "$comp"
      wait_health "$comp" "$old_cid" "$new"
      report_mirror_pod "$comp" "$old_uid" "$new"
      echo "healthy $NODE/$comp backup=$NODE_BACKUP_DIR/$comp.yaml"
    else
      wait_health "$comp" "" "$new"
      report_mirror_pod "$comp" "" "$new"
      echo "healthy $NODE/$comp backup=none"
    fi
  done
  if [[ -n "$NODE_BACKUP_DIR" ]]; then
    APPLIED_NODES+=("$mapping"); APPLIED_DIRS+=("$NODE_BACKUP_DIR")
  fi
  NODE_BACKUP_DIR=""; NODE_CHANGED=()
done
