#!/usr/bin/env bash
set -euo pipefail

kubeconfig="" identity="" known_hosts="$HOME/.ssh/known_hosts" timeout=180
dry_run=false target=true
nodes=() hosts=() ips=() previous=() ssh_opts=()
helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/kubelet-serving-tls-node.sh"

die() { echo "ERROR: $*" >&2; exit 1; }
usage() {
  echo "Usage: $0 --kubeconfig FILE (--node NAME=USER@HOST ... | --local NAME)"
  echo "       [--identity KEY] [--known-hosts FILE] [--ssh-opt OPTION]... [--timeout SECONDS] [--dry-run | --rollback]"
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
    --dry-run) dry_run=true; shift ;;
    --rollback) target=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument $1" ;;
  esac
done
[[ -r "$kubeconfig" && "${#nodes[@]}" -gt 0 && "$timeout" =~ ^[1-9][0-9]*$ ]] || { usage >&2; exit 2; }
[[ "$dry_run" != true || "$target" != false ]] || die "select --dry-run or --rollback"

ssh_cmd=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts")
[[ -z "$identity" ]] || ssh_cmd+=(-i "$identity")
ssh_cmd+=("${ssh_opts[@]+"${ssh_opts[@]}"}")
kube() { kubectl --kubeconfig "$kubeconfig" --request-timeout=10s "$@"; }
on_node() {
  local index="$1"
  shift
  if [[ "${hosts[index]}" == local ]]; then
    if ((EUID == 0)); then bash "$helper" "$@"; else sudo -n bash "$helper" "$@"; fi
  else
    "${ssh_cmd[@]}" "${hosts[index]}" sudo -n bash -s -- "$@" < "$helper"
  fi
}
fingerprint() { openssl x509 -noout -fingerprint -sha256 "$@"; }
verify_peer() { openssl verify -trusted "$work/ca.pem" -purpose sslserver -verify_ip "$1" "$work/peer.pem" >/dev/null; }
kubelet_setting() {
  local value
  yq -e '.kind == "KubeletConfiguration" and .apiVersion == "kubelet.config.k8s.io/v1beta1"' "$1" >/dev/null
  value=$(yq -r '.serverTLSBootstrap | select(tag == "!!bool" or tag == "!!null")' "$1")
  printf '%s\n' "${value/null/absent}"
}

wait_node() {
  local index="$1" require_issuance="$2" old_fp="$3" deadline=$((SECONDS + timeout))
  local node="${nodes[index]}" ip="${ips[index]}" peer_fp issued name certificate csr_fp
  while ((SECONDS < deadline)); do
    if on_node "$index" peer "$ip" > "$work/peer.pem" 2>/dev/null &&
      kube wait "node/$node" --for=condition=Ready --timeout=0s >/dev/null 2>&1; then
      if [[ "$target" == false ]]; then
        # A previously enrolled kubelet should leave managed TLS after restart.
        if ! verify_peer "$ip" 2>/dev/null; then
          echo "$node: fallback TLS listening on $ip:10250"
          return 0
        fi
      elif verify_peer "$ip" 2>/dev/null; then
        peer_fp=$(fingerprint -in "$work/peer.pem") || return 1
        if [[ "$require_issuance" == false ]]; then
          echo "$node: cluster CA and IP SAN verified on $ip:10250 ($peer_fp)"
          return 0
        fi
        if [[ "$peer_fp" != "$old_fp" ]] && issued=$(kube get csr -o json | jq -r --arg node "$node" '
          .items[] | select(.spec.signerName == "kubernetes.io/kubelet-serving" and
            .spec.username == ("system:node:" + $node) and
            any(.status.conditions[]?; .type == "Approved" and .status == "True") and
            ((.status.certificate // "") | length > 0))
          | [.metadata.name, .status.certificate] | @tsv'); then
          while IFS=$'\t' read -r name certificate; do
            [[ -n "$certificate" ]] || continue
            csr_fp=$(printf '%s' "$certificate" | openssl base64 -d -A | fingerprint) || continue
            if [[ "$peer_fp" == "$csr_fp" ]]; then
              echo "$node: CSR $name Approved+Issued, served by $ip:10250 ($peer_fp)"
              openssl x509 -in "$work/peer.pem" -noout -issuer -serial -enddate
              return 0
            fi
          done <<< "$issued"
        fi
      fi
    fi
    sleep 2
  done
  echo "ERROR: timed out waiting for node $node and its :10250 serving TLS" >&2
  return 1
}

work="$(mktemp -d)"
active=""
cleanup() {
  local rc=$?
  trap - EXIT
  if ((rc != 0)) && [[ -n "$active" ]]; then
    # D5: recover the node whose enable failed. Earlier healthy nodes are
    # retained so apply can resume. Escape hatch: run the printed rollback.
    local old="${previous[active]}"
    if [[ "$old" != true && "$(on_node "$active" read "${nodes[active]}")" == true ]]; then
      echo "Restoring ${nodes[active]} to serverTLSBootstrap=$old" >&2
      target=false
      on_node "$active" set true "$old" && wait_node "$active" false "" ||
        echo "ERROR: automatic node recovery failed" >&2
    fi
  fi
  if ((rc != 0)) && [[ "$dry_run" == false ]]; then
    printf 'Rollback command: bash %q' "$0" >&2
    for arg in "${invocation[@]}"; do [[ "$arg" == --rollback ]] || printf ' %q' "$arg" >&2; done
    printf ' --rollback\n' >&2
  fi
  rm -rf "$work"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

kube get nodes -o json > "$work/nodes.json"
registered=$(jq -r '.items[].metadata.name' "$work/nodes.json" | LC_ALL=C sort)
mapped=$(printf '%s\n' "${nodes[@]}" | LC_ALL=C sort)
[[ "$registered" == "$mapped" ]] || die "supply exactly one mapping for each registered node: $registered"
kube -n kube-system get cm kubelet-config -o json > "$work/cm.json"
jq -er '.data.kubelet' "$work/cm.json" > "$work/cm.yaml"
cm_before=$(kubelet_setting "$work/cm.yaml")
[[ "$cm_before" =~ ^(absent|false|true)$ ]] || die "unexpected kubelet ConfigMap setting: $cm_before"
kube -n kube-system get cm kube-root-ca.crt -o json | jq -er '.data["ca.crt"]' > "$work/ca.pem"
openssl x509 -in "$work/ca.pem" -noout

if [[ "$target" == true ]]; then
  if [[ "$dry_run" == false ]]; then
    kube -n kube-system rollout status deploy/kubelet-csr-approver --timeout="${timeout}s"
  fi
  kube -n kube-system get deploy kubelet-csr-approver -o json > "$work/approver.json"
  env_values='.spec.template.spec.containers[] | select(.name == "kubelet-csr-approver") | .env[]'
  name_regex=$(jq -er "$env_values | select(.name == \"PROVIDER_REGEX\") | .value" "$work/approver.json")
  prefixes=$(jq -er "$env_values | select(.name == \"PROVIDER_IP_PREFIXES\") | .value" "$work/approver.json")
  [[ "$name_regex" == '^'*'$' && -n "$prefixes" ]] || die "live approver requires anchored node names and private IP prefixes"
fi

# Preflight every address and SSH identity before restarting anything.
for index in "${!nodes[@]}"; do
  node="${nodes[index]}"
  [[ "$node" =~ ^[a-z0-9.-]+$ ]] || die "invalid node name $node"
  ip=$(jq -er --arg node "$node" '
    [.items[] | select(.metadata.name == $node) | .status.addresses[] | select(.type == "InternalIP") | .address]
    | select(length == 1) | .[0]' "$work/nodes.json")
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "expected one IPv4 InternalIP for $node"
  ips+=("$ip")
  if [[ "$target" == true ]]; then
    [[ "$node" =~ $name_regex ]] || die "$node is outside the approver name policy"
    python3 -c 'from ipaddress import ip_address, ip_network
import sys
sys.exit(not any(ip_address(sys.argv[1]) in ip_network(prefix) for prefix in sys.argv[2].split(",")))' \
      "$ip" "$prefixes" || die "$node InternalIP $ip is outside the approver IP prefixes: $prefixes"
  fi
  before=$(on_node "$index" read "$node") || die "$node: cannot read config or kubelet client identity mismatch"
  [[ "$before" =~ ^(absent|false|true)$ ]] || die "$node: unexpected local setting $before"
  previous+=("$before")
  echo "$node ($ip): serverTLSBootstrap $before -> $target"
done
echo "kube-system/kubelet-config: serverTLSBootstrap $cm_before -> $target"
[[ "$dry_run" == false ]] || exit 0

for index in "${!nodes[@]}"; do
  before="${previous[index]}"
  ip="${ips[index]}"
  old_fp=""
  on_node "$index" peer "$ip" > "$work/peer.pem" 2>/dev/null &&
    old_fp=$(fingerprint -in "$work/peer.pem") || true
  if [[ "$target" == true && "$before" == true && -n "$old_fp" ]] && verify_peer "$ip" 2>/dev/null; then
    # Signed CSRs are garbage-collected. A converged node need not reissue.
    wait_node "$index" false ""
    continue
  fi
  if [[ "$target" == false && "$before" != true && -n "$old_fp" ]] && ! verify_peer "$ip" 2>/dev/null; then
    wait_node "$index" false ""
    continue
  fi
  [[ "$target" == false ]] || active="$index"
  on_node "$index" set "$before" "$target"
  wait_node "$index" "$target" "$old_fp"
  active=""
done

# D6: only publish the setting for future joins after every node passes its
# wire check. Cost: a failed rollout can leave healthy nodes ahead of the CM.
# Escape hatch: re-run apply or roll back all mapped nodes with joins paused.
if [[ "$cm_before" != "$target" && ( "$target" == true || "$cm_before" == true ) ]]; then
  BOOTSTRAP="$target" yq '.serverTLSBootstrap = env(BOOTSTRAP)' "$work/cm.yaml" > "$work/cm-new.yaml"
  jq -c --rawfile cfg "$work/cm-new.yaml" '
    [{op:"test",path:"/metadata/resourceVersion",value:.metadata.resourceVersion},
     {op:"replace",path:"/data/kubelet",value:$cfg}]' "$work/cm.json" > "$work/patch.json"
  kube -n kube-system patch cm kubelet-config --type=json --patch-file "$work/patch.json" -o json |
    jq -er '.data.kubelet' > "$work/cm-patched.yaml"
  [[ "$(kubelet_setting "$work/cm-patched.yaml")" == "$target" ]] || die "kubelet-config patch did not persist $target"
fi
