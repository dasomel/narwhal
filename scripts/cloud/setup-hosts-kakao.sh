#!/usr/bin/env bash
set -euo pipefail

#=========================================
# Narwhal on Kakao Cloud — browser access to *.local.narwhal.internal
#=========================================
# On Vagrant, master-1 runs dnsmasq and the docs tell you to point the resolver at it
# (docs/vagrant/dns-access.md). None of that applies here: PROVIDER=kakao skips dnsmasq, and the
# nodes are on a private subnet your machine cannot reach anyway. The names are served
# by the master LB's public IP (INGRESS_LB=master, the default) and have no public DNS, so /etc/hosts is the way in.
#
# Hostnames are read from the cluster's ApisixRoutes rather than hardcoded, so this
# stays correct as routes are added.
#
# Usage:
#   scripts/cloud/setup-hosts-kakao.sh           # print the lines, change nothing
#   scripts/cloud/setup-hosts-kakao.sh --apply   # write them to /etc/hosts (needs sudo)
#   scripts/cloud/setup-hosts-kakao.sh --remove  # take them back out
#   INGRESS_LB=worker scripts/cloud/setup-hosts-kakao.sh --apply  # use the worker LB instead
#
# Needs a working kubectl context for the cluster — run scripts/cloud/set-config-kakao.sh
# first.

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
DOMAIN="${DOMAIN:-kakao.narwhal.internal}"
MARK_BEGIN="# BEGIN narwhal-kakao"
MARK_END="# END narwhal-kakao"
HOSTS_FILE="${HOSTS_FILE:-/etc/hosts}"
ACTION="${1:-print}"

cd "$(dirname "$0")/../.."
[ -d "${TF_DIR}" ] || { echo "ERROR: ${TF_DIR} not found — run from the narwhal repo" >&2; exit 1; }

remove_block() {
  # Deleting between markers is why they exist — a plain grep -v on the LB address
  # would also strip unrelated entries that happen to share it.
  sudo sed -i '' "/^${MARK_BEGIN}\$/,/^${MARK_END}\$/d" "${HOSTS_FILE}" 2>/dev/null \
    || sudo sed -i "/^${MARK_BEGIN}\$/,/^${MARK_END}\$/d" "${HOSTS_FILE}"
}

if [ "${ACTION}" = "--remove" ]; then
  remove_block
  echo "Removed the narwhal-kakao block from ${HOSTS_FILE}."
  exit 0
fi

# D-INGRESS: both LBs forward 80/443 to the same ingress NodePorts (31080/31443), so either
# one serves the names. Default is master.
#   Reason     : the worker LB public IP stopped answering from the internet (verified live
#                2026-10-23/25: ONLINE/ACTIVE and reachable from inside the VPC, yet ICMP+TCP
#                dropped at the Kakao edge and external SYNs never reached a worker); the master
#                LB public IP returns HTTP 302 externally.
#   Cost       : the master ingress target groups (masters_http/masters_https) have no health
#                monitor, so a failed master can cause intermittent failures.
#   Escape hatch: INGRESS_LB=worker (e.g. once the worker LB public path is healthy again).
INGRESS_LB="${INGRESS_LB:-master}"
case "${INGRESS_LB}" in
  worker|master) ;;
  *) echo "ERROR: INGRESS_LB must be worker or master (got '${INGRESS_LB}')" >&2; exit 1 ;;
esac
LB=$(cd "${TF_DIR}" && tofu output -raw "${INGRESS_LB}_lb_public_ip" 2>/dev/null || true)
[ -n "${LB}" ] && [ "${LB}" != "null" ] || {
  echo "ERROR: tofu output ${INGRESS_LB}_lb_public_ip is empty — apply ${TF_DIR} first" >&2
  exit 1
}

# "in_use" and ONLINE in the API do not prove the public path works, and an /etc/hosts
# entry pointing at a dead address fails as a silent browser timeout. Probe from here.
# Only connect failure (7) and timeout (28) mean unreachable; a TLS or HTTP error from a
# bare-IP request without SNI still proves the path is open.
probe_rc=0
if [ "${ACTION}" = "--apply" ] && [ "${SKIP_PROBE:-0}" != "1" ]; then
  curl -sk -m 5 -o /dev/null "https://${LB}/" 2>/dev/null || probe_rc=$?
fi
if [ "${probe_rc}" -eq 7 ] || [ "${probe_rc}" -eq 28 ]; then
  echo "ERROR: ${INGRESS_LB} LB ${LB}:443 is not reachable from this machine." >&2
  if [ "${INGRESS_LB}" = "master" ]; then
    other=worker
  else
    other=master
  fi
  echo "       Try the ${other} LB, which serves the same NodePorts:" >&2
  echo "         INGRESS_LB=${other} $0 --apply" >&2
  echo "       SKIP_PROBE=1 writes the entry anyway." >&2
  exit 1
fi

kubectl get apisixroute -A >/dev/null 2>&1 || {
  echo "ERROR: cannot reach the cluster with the current kubectl context." >&2
  echo "       Run scripts/cloud/set-config-kakao.sh first (it opens the tunnel)." >&2
  exit 1
}

HOSTS=$(kubectl get apisixroute -A -o json | python3 -c '
import json, sys
hosts = set()
for item in json.load(sys.stdin)["items"]:
    for rule in (item["spec"].get("http") or []):
        for h in (rule.get("match", {}).get("hosts") or []):
            hosts.add(h)
print(" ".join(sorted(hosts)))
')
[ -n "${HOSTS}" ] || { echo "ERROR: no hostnames found in any ApisixRoute" >&2; exit 1; }

COUNT=$(printf '%s' "${HOSTS}" | wc -w | tr -d ' ')
BLOCK="${MARK_BEGIN}
${LB} ${HOSTS}
${MARK_END}"

echo "Ingress LB: ${LB} (${INGRESS_LB})"
echo "Hostnames : ${COUNT} (from ApisixRoutes)"
echo ""

if [ "${ACTION}" != "--apply" ]; then
  echo "${BLOCK}"
  echo ""
  echo "Add with:    $0 --apply"
  echo "Remove with: $0 --remove"
  exit 0
fi

remove_block
printf '%s\n' "${BLOCK}" | sudo tee -a "${HOSTS_FILE}" >/dev/null
echo "Wrote ${COUNT} hostnames to ${HOSTS_FILE}."
echo ""

# The platform issues its own certificates, so a browser will warn until this CA is
# trusted. Exporting it is safe and reversible; installing it is left to you because it
# changes a system trust store.
CA_OUT="${CA_OUT:-./narwhal-ca.crt}"
if kubectl get secret narwhal-ca-cert -n devtools >/dev/null 2>&1; then
  kubectl get secret narwhal-ca-cert -n devtools -o jsonpath='{.data.ca\.crt}' \
    | base64 -d > "${CA_OUT}" 2>/dev/null || true
fi

if [ -s "${CA_OUT}" ]; then
  echo "Certificates are issued by '$(openssl x509 -in "${CA_OUT}" -noout -issuer 2>/dev/null | sed 's/^issuer=//')'."
  echo "Saved to ${CA_OUT}. Browsers warn until it is trusted:"
  echo ""
  echo "  macOS : sudo security add-trusted-cert -d -r trustRoot \\"
  echo "            -k /Library/Keychains/System.keychain ${CA_OUT}"
  echo "  Linux : sudo cp ${CA_OUT} /usr/local/share/ca-certificates/narwhal.crt && sudo update-ca-certificates"
  echo ""
  echo "  Undo (macOS): sudo security delete-certificate -c 'Narwhal IDP Root CA' /Library/Keychains/System.keychain"
else
  echo "NOTE: could not export the CA (secret narwhal-ca-cert in devtools)."
  echo "      Browsers will warn about the self-signed certificate."
fi

echo ""
echo "Open any of these:"
# shellcheck disable=SC2086  # HOSTS is a space-separated list; splitting is the point
printf '%s\n' ${HOSTS} | sed 's#^#  https://#' | head -20
