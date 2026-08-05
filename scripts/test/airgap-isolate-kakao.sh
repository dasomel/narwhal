#!/bin/bash
# Cut the Kakao nodes off the internet so an airgap claim can actually be tested.
#
# Why this exists: stopping squid is not enough. The Kakao VPC has a working egress
# path of its own, so a node with the proxy down still reaches get.helm.sh and
# raw.githubusercontent.com directly (measured 2026-08-05: 404 and 301, i.e. connected).
# Any script that still fetches from the internet would therefore pass a "proxy off"
# test and fail on a real closed network — the same class of blind spot as the live
# proxy that hid the missing chart mirror on 2026-07-28, just one layer down.
#
# What it blocks: everything leaving the node except the VPC, the pod/service CIDRs,
# loopback and link-local (cloud metadata). REJECT rather than DROP, deliberately —
# Kakao security groups drop, and three separate incidents in this project were spent
# chasing a hang that a fast failure would have named immediately.
#
# Scope limit, stated rather than papered over: this filters the OUTPUT chain, so it
# covers host processes — containerd image pulls, helm, kubectl, curl in the install
# scripts. That is exactly what the airgap bundle is supposed to serve. Pod egress
# rides Cilium's eBPF datapath and does not traverse these rules; isolating that is a
# different exercise (network policy), not this one.
#
# Relationship to AIRGAP=1: `01-prerequisites.sh` already drops the default route for the
# same reason, and that is the better mechanism — it is part of the install, so it runs on
# every airgap provision rather than only when someone remembers this script. Use this when
# the nodes are already past that point (base provisioned with a proxy, say) and you want to
# isolate them without rebuilding, or to confirm isolation independently of the thing being
# tested. If you are starting a run from scratch, prefer AIRGAP=1.
#
# Rules live in memory only and are gone after a reboot. Fine for a test run; do not
# mistake this for a hardening measure.
#
# Usage:
#   scripts/test/airgap-isolate-kakao.sh on     # block
#   scripts/test/airgap-isolate-kakao.sh off    # restore
#   scripts/test/airgap-isolate-kakao.sh status
set -euo pipefail

ACTION="${1:-status}"
TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"
CHAIN="NARWHAL_AIRGAP"

# Reachable from an isolated node. Everything else is rejected.
ALLOW_NETS="172.17.0.0/16 10.244.0.0/16 10.96.0.0/12 127.0.0.0/8 169.254.0.0/16"

cd "$(dirname "$0")/../.."

BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac

node_json=$(cd "${TF_DIR}" && tofu output -json master_private_ips && tofu output -json worker_private_ips)
NODES=()
while IFS= read -r _ip; do
  [ -n "${_ip}" ] && NODES+=("${_ip}")
done < <(printf '%s\n' "${node_json}" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if line:
        for ip in json.loads(line):
            print(ip)
')

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

# Building the chain fresh each time keeps `on` idempotent: flush, refill, and only
# then hook it into OUTPUT if it is not already there.
apply_on() {
  local allow_rules=""
  for net in ${ALLOW_NETS}; do
    allow_rules="${allow_rules} sudo iptables -A ${CHAIN} -d ${net} -j RETURN;"
  done
  ssh_node "$1" "
    set -e
    sudo iptables -N ${CHAIN} 2>/dev/null || sudo iptables -F ${CHAIN}
    sudo iptables -A ${CHAIN} -o lo -j RETURN
    ${allow_rules}
    sudo iptables -A ${CHAIN} -j REJECT --reject-with icmp-admin-prohibited
    sudo iptables -C OUTPUT -j ${CHAIN} 2>/dev/null || sudo iptables -I OUTPUT 1 -j ${CHAIN}
    echo '    isolated'
  "
}

apply_off() {
  ssh_node "$1" "
    sudo iptables -D OUTPUT -j ${CHAIN} 2>/dev/null || true
    sudo iptables -F ${CHAIN} 2>/dev/null || true
    sudo iptables -X ${CHAIN} 2>/dev/null || true
    echo '    restored'
  "
}

# The check that matters is behavioural, not a rule dump: can the node still reach a
# public host, and can it still reach the registry it is supposed to depend on.
show_status() {
  ssh_node "$1" "
    hooked=\$(sudo iptables -C OUTPUT -j ${CHAIN} 2>/dev/null && echo yes || echo no)
    # curl prints 000 via -w *and* exits non-zero, so an '|| echo' here would emit
    # both and read as '000blocked'. Take the code, then name it.
    ext=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --noproxy '*' https://raw.githubusercontent.com/ 2>/dev/null || true)
    reg=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --noproxy '*' http://172.17.0.168:5000/v2/ 2>/dev/null || true)
    [ \"\${ext}\" = 000 ] && ext='blocked'
    [ \"\${reg}\" = 000 ] && reg='unreachable'
    echo \"    chain=\${hooked}  internet=\${ext}  registry=\${reg}\"
  "
}

case "${ACTION}" in
  on)     echo "=== Isolating ${#NODES[@]} node(s) from the internet ===" ;;
  off)    echo "=== Restoring egress on ${#NODES[@]} node(s) ===" ;;
  status) echo "=== Isolation status ===" ;;
  *) echo "usage: $0 {on|off|status}" >&2; exit 1 ;;
esac

for ip in "${NODES[@]}"; do
  echo "  ${ip}"
  case "${ACTION}" in
    on)  apply_on "${ip}"; show_status "${ip}" ;;
    off) apply_off "${ip}"; show_status "${ip}" ;;
    status) show_status "${ip}" ;;
  esac
done
