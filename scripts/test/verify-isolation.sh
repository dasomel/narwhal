#!/usr/bin/env bash
set -euo pipefail

#=========================================
# verify-isolation.sh — is this cluster actually cut off from the internet?
#=========================================
# Asks each node the questions that caught real gaps, not the ones that look right:
#
#   route    default route absent. `proto dhcp` in the answer means DHCP renewal
#            resurrected it (2026-08-05: all six Kakao nodes, hours after removal).
#   dropin   networkd drop-in present — the thing that makes removal survive renewal.
#            A node can pass `route` and fail this: isolated now, open after the
#            next lease.
#   direct   `curl --noproxy '*'` to a public host must FAIL. This is the probe that
#            proved "proxy stopped" ≠ "offline" — the Kakao VPC has its own egress,
#            and a stopped squid hid nothing (2026-08-05).
#   mirror   the bundle registry must still answer — isolation that also cuts the
#            mirror is a dead cluster, not an airgapped one.
#   apt      no active online sources; only file:///srv/airgap/apt.
#   meta     (kakao) the 169.254.169.254 route survives — UseRoutes=false used to
#            discard it with the gateway and break cloud-init on reboot (2026-08-06).
#
# Read-only. To CHANGE isolation state, see docs/common/airgap-isolation-testing.md
# (durable mechanism) or scripts/test/airgap-isolate-kakao.sh (iptables, test-only).
#
# Usage:
#   scripts/test/verify-isolation.sh local            # Vagrant cluster, expect isolated
#   scripts/test/verify-isolation.sh kakao            # Kakao cluster,   expect isolated
#   EXPECT=open scripts/test/verify-isolation.sh kakao  # deliberately un-isolated:
#                                                       # 'direct' must SUCCEED instead
#
# Exit code = number of failing nodes.

PROVIDER="${1:-local}"
EXPECT="${EXPECT:-isolated}"
TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
PROBE_URL="${PROBE_URL:-https://raw.githubusercontent.com/}"

cd "$(dirname "$0")/../.."

case "${PROVIDER}" in
  kakao)
    REGISTRY="${AIRGAP_REGISTRY:-$(cd "${TF_DIR}" && tofu output -raw bastion_private_ip):5000}"
    BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
    SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
    [ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
    case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
    node_json=$(cd "${TF_DIR}" && tofu output -json master_private_ips && tofu output -json worker_private_ips)
    NODES=$(printf '%s\n' "${node_json}" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if line:
        for ip in json.loads(line):
            print(ip)')
    ;;
  local)
    REGISTRY="${AIRGAP_REGISTRY:-192.168.56.1:5001}"
    NODES=$(vagrant status --machine-readable 2>/dev/null \
      | awk -F, '$3=="state" && $4=="running" {print $2}')
    [ -n "${NODES}" ] || { echo "ERROR: no running Vagrant machines" >&2; exit 1; }
    ;;
  *) echo "usage: $0 {local|kakao}" >&2; exit 1 ;;
esac

# One shell script, run on every node whichever way we can reach it. Emits
# "key=value" lines; judgement happens back here so local and kakao nodes are
# held to the identical standard.
remote_probe() {
  cat <<PROBE
route=\$(ip route show default 2>/dev/null | awk '{print \$3; exit}')
dropin=\$(ls /etc/systemd/network/*.d/airgap.conf 2>/dev/null | awk 'NR==1{print "yes"} END{if(NR==0) print "no"}')
direct=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --noproxy '*' ${PROBE_URL} 2>/dev/null || true)
mirror=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --noproxy '*' http://${REGISTRY}/v2/ 2>/dev/null || true)
apt_online=\$(grep -rhE '^deb (http|https)' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null | grep -cv '^\$' || true)
apt_bundle=\$(grep -rhcE '^deb \[trusted=yes\] file:///srv/airgap/apt' /etc/apt/sources.list.d/ 2>/dev/null | paste -sd+ - | bc 2>/dev/null || echo 0)
meta=\$(ip route show 169.254.169.254 2>/dev/null | awk '{print "yes"; exit}')
echo "route=\${route:-none} dropin=\${dropin} direct=\${direct:-blocked} mirror=\${mirror:-down} apt_online=\${apt_online:-0} apt_bundle=\${apt_bundle:-0} meta=\${meta:-no}"
PROBE
}

run_on() {
  local node="$1"
  case "${PROVIDER}" in
    kakao)
      ssh -i "${SSH_KEY}" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=10 \
        -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -W %h:%p ubuntu@${BASTION_IP}" \
        "ubuntu@${node}" 'bash -s' ;;
    local)
      vagrant ssh "${node}" -c 'bash -s' 2>/dev/null | tr -d '\r' ;;
  esac
}

echo "== isolation check: provider=${PROVIDER} expect=${EXPECT} registry=${REGISTRY}"
FAILED=0
for node in ${NODES}; do
  line=$(remote_probe | run_on "${node}" | grep '^route=' || true)
  if [ -z "${line}" ]; then
    echo "  ${node}  UNREACHABLE"
    FAILED=$((FAILED + 1))
    continue
  fi
  # shellcheck disable=SC2086  # the probe emits shell-safe key=value words
  local_route=""; local_dropin=""; local_direct=""; local_mirror=""
  local_apt_online=""; local_apt_bundle=""; local_meta=""
  for kv in ${line}; do eval "local_${kv%%=*}=\${kv#*=}"; done

  verdict="ok"
  reasons=""
  if [ "${EXPECT}" = "isolated" ]; then
    [ "${local_route}" = "none" ]      || { verdict=FAIL; reasons="${reasons}route-present "; }
    [ "${local_dropin}" = "yes" ]      || { verdict=FAIL; reasons="${reasons}no-dropin "; }
    [ "${local_direct}" = "blocked" ] || [ "${local_direct}" = "000" ] \
                                       || { verdict=FAIL; reasons="${reasons}internet-open(${local_direct}) "; }
  else
    [ "${local_route}" != "none" ]     || { verdict=FAIL; reasons="${reasons}no-route "; }
    case "${local_direct}" in
      2*|3*) ;; *) verdict=FAIL; reasons="${reasons}internet-closed(${local_direct}) " ;;
    esac
  fi
  [ "${local_mirror}" = "200" ]        || { verdict=FAIL; reasons="${reasons}mirror(${local_mirror}) "; }
  [ "${local_apt_online}" = "0" ]      || { verdict=FAIL; reasons="${reasons}apt-online(${local_apt_online}) "; }
  [ "${local_apt_bundle}" != "0" ]     || { verdict=FAIL; reasons="${reasons}apt-bundle-missing "; }
  if [ "${PROVIDER}" = "kakao" ] && [ "${local_meta}" != "yes" ]; then
    verdict=FAIL; reasons="${reasons}metadata-route-lost "
  fi

  printf '  %-12s %-4s  %s' "${node}" "${verdict}" "${line}"
  [ -n "${reasons}" ] && printf '   <- %s' "${reasons}"
  echo ""
  [ "${verdict}" = "ok" ] || FAILED=$((FAILED + 1))
done

echo ""
if [ "${FAILED}" = "0" ]; then
  echo "== all nodes match expect=${EXPECT}"
else
  echo "== ${FAILED} node(s) do NOT match expect=${EXPECT}"
fi
exit "${FAILED}"
