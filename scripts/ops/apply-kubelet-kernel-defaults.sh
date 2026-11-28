#!/usr/bin/env bash
# Roll out CIS 4.2.6 (kubelet protectKernelDefaults=true) with strict sysctl-before-kubelet ordering.
# Dry-run by default; pass --apply to mutate or --rollback to revert protectKernelDefaults=false.
set -euo pipefail

kubeconfig="" identity="" known_hosts="$HOME/.ssh/known_hosts" timeout=180
apply=false rollback=false target=true
nodes=() hosts=() previous=() ssh_opts=()
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
helper="$script_dir/lib/kubelet-kernel-defaults-node.sh"
sysctl_conf="$script_dir/lib/95-kubelet-kernel-defaults.conf"

die() { echo "ERROR: $*" >&2; exit 1; }
usage() {
  echo "Usage: $0 --kubeconfig FILE (--node NAME=USER@HOST ... | --local NAME)"
  echo "       [--identity KEY] [--known-hosts FILE] [--ssh-opt OPTION]... [--timeout SECONDS]"
  echo "       [--apply | --rollback]"
}
invocation=("$@")
while (($#)); do
  case "$1" in
    --kubeconfig) kubeconfig="$2"; shift 2 ;;
    --node)
      [[ "$2" =~ ^[a-z0-9.-]+=[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+$ ]] || die "expected NAME=USER@HOST"
      nodes+=("${2%%=*}"); hosts+=("${2#*=}"); shift 2 ;;
    --local) nodes+=("$2"); hosts+=("local"); shift 2 ;;
    --identity) identity="$2"; shift 2 ;;
    --known-hosts) known_hosts="$2"; shift 2 ;;
    --ssh-opt) ssh_opts+=(-o "${2:?--ssh-opt requires an OpenSSH option}"); shift 2 ;;
    --timeout) timeout="$2"; shift 2 ;;
    --apply) apply=true; shift ;;
    --rollback) rollback=true; target=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument $1" ;;
  esac
done
[[ -r "$kubeconfig" && -r "$helper" && -r "$sysctl_conf" && "${#nodes[@]}" -gt 0 && "$timeout" =~ ^[1-9][0-9]*$ ]] || { usage >&2; exit 2; }
[[ "$apply" != true || "$rollback" != true ]] || die "select --apply or --rollback"
if [[ "$rollback" == true ]]; then apply=true; fi

ssh_cmd=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts")
[[ -z "$identity" ]] || ssh_cmd+=(-i "$identity")
ssh_cmd+=("${ssh_opts[@]+"${ssh_opts[@]}"}")
kube() { kubectl --kubeconfig "$kubeconfig" --request-timeout=10s "$@"; }

on_node() {
  local index="$1"; shift
  if [[ "${hosts[index]}" == local ]]; then
    if ((EUID == 0)); then bash "$helper" "$@"; else sudo -n bash "$helper" "$@"; fi
  else
    "${ssh_cmd[@]}" "${hosts[index]}" sudo -n bash -s -- "$@" < "$helper"
  fi
}

kubelet_setting() {
  local value
  yq -e '.kind == "KubeletConfiguration" and .apiVersion == "kubelet.config.k8s.io/v1beta1"' "$1" >/dev/null
  value=$(yq -r '.protectKernelDefaults | select(tag == "!!bool" or tag == "!!null")' "$1")
  printf '%s\n' "${value/null/absent}"
}

wait_node() {
  local index="$1" deadline=$((SECONDS + timeout))
  local node="${nodes[index]}"
  while ((SECONDS < deadline)); do
    if on_node "$index" is-active && kube wait "node/$node" --for=condition=Ready --timeout=0s >/dev/null 2>&1; then
      echo "$node: kubelet active and Ready"
      return 0
    fi
    sleep 2
  done
  echo "ERROR: timed out waiting for node $node kubelet Ready" >&2
  return 1
}

patch_cm() {
  local desired="$1"
  kube -n kube-system get cm kubelet-config -o json > "$work/cm.json"
  jq -er '.data.kubelet' "$work/cm.json" > "$work/cm.yaml"
  local current
  current=$(kubelet_setting "$work/cm.yaml")
  if [[ "$current" == "$desired" || ( "$desired" == false && "$current" != true ) ]]; then
    echo "kube-system/kubelet-config: protectKernelDefaults already $current"
    return 0
  fi
  PROTECT="$desired" yq '.protectKernelDefaults = env(PROTECT)' "$work/cm.yaml" > "$work/cm-new.yaml"
  jq -c --rawfile cfg "$work/cm-new.yaml" '
    [{op:"test",path:"/metadata/resourceVersion",value:.metadata.resourceVersion},
     {op:"replace",path:"/data/kubelet",value:$cfg}]' "$work/cm.json" > "$work/patch.json"
  kube -n kube-system patch cm kubelet-config --type=json --patch-file "$work/patch.json" -o json |
    jq -er '.data.kubelet' > "$work/cm-patched.yaml"
  [[ "$(kubelet_setting "$work/cm-patched.yaml")" == "$desired" ]] || die "kubelet-config patch did not persist $desired"
  echo "kube-system/kubelet-config: protectKernelDefaults $current -> $desired"
}

work="$(mktemp -d)"
active=""
cleanup() {
  local rc=$?
  trap - EXIT
  if ((rc != 0)) && [[ -n "$active" ]]; then
    local old="${previous[active]}"
    if [[ "$old" != true && "$(on_node "$active" read "${nodes[active]}" 2>/dev/null || true)" == true ]]; then
      echo "Restoring ${nodes[active]} to protectKernelDefaults=$old" >&2
      on_node "$active" set true "$old" >/dev/null && wait_node "$active" ||
        echo "ERROR: automatic node recovery failed for ${nodes[active]}" >&2
    fi
  fi
  if ((rc != 0)) && [[ "$apply" == true && "$target" == true ]]; then
    printf 'Rollback command: bash %q' "$0" >&2
    for arg in "${invocation[@]}"; do [[ "$arg" == --apply || "$arg" == --rollback ]] || printf ' %q' "$arg" >&2; done
    printf ' --rollback\n' >&2
  fi
  rm -rf "$work"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Require exact 1:1 mapping of every registered node so kubelet-config CM never outruns sysctls.
kube get nodes -o json > "$work/nodes.json"
registered=$(jq -r '.items[].metadata.name' "$work/nodes.json" | LC_ALL=C sort)
mapped=$(printf '%s\n' "${nodes[@]}" | LC_ALL=C sort)
[[ "$registered" == "$mapped" ]] || die "supply exactly one mapping for each registered node: $registered"

kube -n kube-system get cm kubelet-config -o json > "$work/cm.json"
jq -er '.data.kubelet' "$work/cm.json" > "$work/cm.yaml"
cm_before=$(kubelet_setting "$work/cm.yaml")
[[ "$cm_before" =~ ^(absent|false|true)$ ]] || die "unexpected kubelet-config setting: $cm_before"

for index in "${!nodes[@]}"; do
  node="${nodes[index]}"
  [[ "$node" =~ ^[a-z0-9.-]+$ ]] || die "invalid node name $node"
  before=$(on_node "$index" read "$node") || die "$node: cannot read kubelet config or client identity mismatch"
  [[ "$before" =~ ^(absent|false|true)$ ]] || die "$node: unexpected local setting $before"
  previous+=("$before")
  echo "$node: protectKernelDefaults $before -> $target"
done
echo "kube-system/kubelet-config: protectKernelDefaults $cm_before -> $target"
[[ "$apply" == true ]] || { echo "dry-run complete (no remote or cluster writes)"; exit 0; }

if [[ "$target" == true ]]; then
  # Phase 1: persist and verify all 6 sysctls on EVERY node BEFORE restarting any kubelet.
  sysctl_b64=$(base64 < "$sysctl_conf" | tr -d '\n')
  for index in "${!nodes[@]}"; do
    on_node "$index" apply-sysctl "$sysctl_b64" || die "${nodes[index]}: CIS 4.2.6 sysctl setup/verification failed — aborting before touching kubelet"
    echo "${nodes[index]}: sysctls persisted and verified (/etc/sysctl.d/95-kubelet-kernel-defaults.conf)"
  done

  # Phase 2: enable protectKernelDefaults serially per node.
  for index in "${!nodes[@]}"; do
    before="${previous[index]}"
    if [[ "$before" == true ]]; then
      wait_node "$index"
      continue
    fi
    active="$index"
    backup=$(on_node "$index" set "$before" true)
    wait_node "$index"
    echo "${nodes[index]}: protectKernelDefaults=true backup=$backup"
    active=""
  done

  # Phase 3: publish to kube-system/kubelet-config only after every node is Ready.
  patch_cm true
else
  # Rollback order: revert kubelet-config CM first, then each node serially.
  patch_cm false
  for index in "${!nodes[@]}"; do
    before="${previous[index]}"
    if [[ "$before" != true ]]; then
      wait_node "$index"
      continue
    fi
    backup=$(on_node "$index" set "$before" false)
    wait_node "$index"
    echo "${nodes[index]}: protectKernelDefaults=false backup=$backup"
  done
fi
