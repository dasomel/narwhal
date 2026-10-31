#!/bin/bash
# Hand the join artifacts from master-1 to the other Kakao Cloud nodes.
#
# On Vagrant, 02-join-{control-plane,worker}.sh fetch these themselves with
# `sshpass -p vagrant scp` from master-1. A cloud image has no vagrant password and
# no node-to-node trust, so both scripts take a PROVIDER=kakao branch that expects
# the files to already be in /tmp — "operator-supplied". This is that operator.
#
# Everything moves host -> bastion -> node; nodes never talk to each other.
#
#   /home/vagrant/join-control-plane.sh  -> master-2, master-3  as /tmp/join-control-plane.sh
#   /home/vagrant/encryption-config.yaml -> master-2, master-3  as /tmp/encryption-config.yaml
#   /etc/kubernetes/pki/oidc-ca.crt      -> master-2, master-3  as /tmp/oidc-ca.crt
#       only when kubeadm-config references oidc-ca-file (D-KUBEADM-CONFIG-OIDC, #505): a
#       master joined later renders its apiserver from that config and cannot start without the
#       CA file. Required but unobtainable -> this script stops before placing anything.
#   /home/vagrant/join-command.sh        -> workers             as /tmp/join-command.sh
#
# The certificate key inside join-control-plane.sh expires with the kubeadm-certs
# secret (2h by default), and the bootstrap token after 24h. Re-run
# `kubeadm init phase upload-certs --upload-certs` on master-1 and regenerate before
# re-running this if a join fails on an expired key.
#
# Usage (from the repo root, after 02-init-cluster.sh has succeeded on master-1):
#   ./scripts/cloud/distribute-join-kakao.sh
set -euo pipefail

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"

cd "$(dirname "$0")/../.."

BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
[ -f "${SSH_KEY}" ] || { echo "ERROR: private key not found at ${SSH_KEY}" >&2; exit 1; }

# shellcheck source=scripts/cloud/lib-ssh.sh
source scripts/cloud/lib-ssh.sh

mapfile_masters=$(cd "${TF_DIR}" && tofu output -json master_private_ips)
mapfile_workers=$(cd "${TF_DIR}" && tofu output -json worker_private_ips)
read_ips() {
  printf '%s' "$1" | python3 -c 'import json,sys
for ip in json.load(sys.stdin): print(ip)'
}

MASTERS=()
while IFS= read -r _ip; do [ -n "${_ip}" ] && MASTERS+=("${_ip}"); done < <(read_ips "${mapfile_masters}")
WORKERS=()
while IFS= read -r _ip; do [ -n "${_ip}" ] && WORKERS+=("${_ip}"); done < <(read_ips "${mapfile_workers}")

MASTER1="${MASTERS[0]}"

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    "${KAKAO_SSH_OPTS[@]}" \
    -o ProxyCommand="ssh -i ${SSH_KEY} ${KAKAO_SSH_OPTS[*]} -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

echo "=== Collecting join artifacts from master-1 (${MASTER1}) ==="
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
for f in join-control-plane.sh join-command.sh encryption-config.yaml; do
  # sudo cat, not scp: the files are owned by vagrant with mode 600.
  if ! ssh_node "${MASTER1}" "sudo cat /home/vagrant/${f}" > "${tmp}/${f}" 2>/dev/null || [ ! -s "${tmp}/${f}" ]; then
    echo "ERROR: ${f} missing or empty on master-1 — has 02-init-cluster.sh finished?" >&2
    exit 1
  fi
  echo "  ${f} ($(wc -c < "${tmp}/${f}") bytes)"
done

# D-KUBEADM-CONFIG-OIDC (#505): decide here, on the operator host, whether the joining masters
# need the OIDC CA (nodes cannot ask master-1 themselves). Unreadable config or a missing/invalid
# CA while the config references it is fatal: nothing has been placed yet.
# Expected OIDC CA hash: the narwhal.dasomel.io/oidc-ca-sha256 annotation 11-4b writes on each
# kube-apiserver it configured. A master configured before #500 carries none and is ignored;
# every present value must be a sha256 and all must agree. Prints the hash, or an error to
# stderr and returns 1 (no kubectl/API, no annotation, a malformed value, disagreement).
oidc_expected_ca_sha() {
  local ctx="${KAKAO_KUBE_CONTEXT:-narwhal-kakao}" values present uniq
  if ! values="$(kubectl --context "${ctx}" --request-timeout=20s -n kube-system \
    get pods -l component=kube-apiserver \
    -o jsonpath='{range .items[*]}{.metadata.annotations.narwhal\.dasomel\.io/oidc-ca-sha256}{"\n"}{end}')"; then
    echo "ERROR: cannot list kube-apiserver pods via context '${ctx}' (kubectl missing, API unreachable or no pod read permission) — not distributing; run scripts/cloud/set-config-kakao.sh or set KAKAO_KUBE_CONTEXT" >&2
    return 1
  fi
  present="$(grep -v '^[[:space:]]*$' <<<"${values}" || true)"
  if [ -z "${present}" ]; then
    echo "ERROR: no kube-apiserver in context '${ctx}' carries oidc-ca-sha256 — cannot verify the OIDC CA; re-run 11-4b on a master first — not distributing" >&2
    return 1
  fi
  if grep -qvE '^[0-9a-f]{64}$' <<<"${present}"; then
    echo "ERROR: a kube-apiserver oidc-ca-sha256 annotation in context '${ctx}' is not a sha256 — not distributing" >&2
    return 1
  fi
  uniq="$(sort -u <<<"${present}")"
  if [ "$(wc -l <<<"${uniq}" | tr -d ' ')" != "1" ]; then
    echo "ERROR: kube-apiservers in context '${ctx}' disagree on oidc-ca-sha256 — not distributing" >&2
    return 1
  fi
  printf '%s\n' "${uniq}"
}

OIDC_CA_REQUIRED=false
if ! cluster_config="$(ssh_node "${MASTER1}" "sudo env KUBECONFIG=/home/vagrant/.kube/config-local kubectl -n kube-system get cm kubeadm-config -o jsonpath='{.data.ClusterConfiguration}'")" \
  || [ -z "${cluster_config}" ]; then
  echo "ERROR: cannot read kube-system/kubeadm-config on master-1; cannot tell whether the joining masters need the OIDC CA" >&2
  exit 1
fi
if grep -q 'oidc-ca-file' <<<"${cluster_config}"; then
  OIDC_CA_REQUIRED=true
  # D-JOIN-OIDC-CA-HASH (#522): the operator's kubeconfig cluster CA authenticates the
  # API channel independently of the SSH copy. The context is pinned (KAKAO_KUBE_CONTEXT,
  # default narwhal-kakao as set-config-kakao.sh names it) so the shared current-context
  # cannot point this at another cluster. Cost: a reachable API and pod-read permission;
  # escape hatch: run scripts/cloud/set-config-kakao.sh (or set KAKAO_KUBE_CONTEXT) and retry.
  if ! expected_ca_sha="$(oidc_expected_ca_sha)"; then
    exit 1
  fi
  if ! ssh_node "${MASTER1}" "sudo cat /etc/kubernetes/pki/oidc-ca.crt" > "${tmp}/oidc-ca.crt" 2>/dev/null \
    || [ ! -s "${tmp}/oidc-ca.crt" ] || ! openssl x509 -in "${tmp}/oidc-ca.crt" -noout -subject >/dev/null 2>&1; then
    echo "ERROR: kubeadm-config references oidc-ca-file but /etc/kubernetes/pki/oidc-ca.crt on master-1 is missing or not a valid certificate; a joined master would crashloop" >&2
    exit 1
  fi
  copied_ca_sha="$(openssl dgst -sha256 -r "${tmp}/oidc-ca.crt" | cut -d' ' -f1)"
  if [ "${copied_ca_sha}" != "${expected_ca_sha}" ]; then
    echo "ERROR: OIDC CA hash mismatch with master-1 API annotation — not distributing; run scripts/cloud/set-config-kakao.sh and verify the narwhal-kakao context (or your configured Kakao context), then repair the CA/annotation" >&2
    exit 1
  fi
  echo "  oidc-ca.crt ($(wc -c < "${tmp}/oidc-ca.crt") bytes)"
fi

place() {
  local ip="$1" src="$2" dest="$3"
  ssh_node "${ip}" "cat > '${dest}' && chmod 0600 '${dest}'" < "${tmp}/${src}"
}

for ip in "${MASTERS[@]:1}"; do
  echo "=== control-plane ${ip} ==="
  place "${ip}" join-control-plane.sh /tmp/join-control-plane.sh
  place "${ip}" encryption-config.yaml /tmp/encryption-config.yaml
  if [ "${OIDC_CA_REQUIRED}" = "true" ]; then
    place "${ip}" oidc-ca.crt /tmp/oidc-ca.crt
  fi
  ssh_node "${ip}" "ls -l /tmp/join-control-plane.sh /tmp/encryption-config.yaml | awk '{print \"    \"\$5, \$9}'"
done

for ip in "${WORKERS[@]}"; do
  echo "=== worker ${ip} ==="
  place "${ip}" join-command.sh /tmp/join-command.sh
  ssh_node "${ip}" "ls -l /tmp/join-command.sh | awk '{print \"    \"\$5, \$9}'"
done

echo ""
echo "=== Distributed to $(( ${#MASTERS[@]} - 1 )) control-plane + ${#WORKERS[@]} worker node(s) ==="
# sudo -E is refused on these images ("preserving the entire environment is not
# supported"), so the variable has to ride on `sudo env` or it never arrives.
echo "Next, on each node:"
echo "  control-plane: sudo env PROVIDER=kakao bash scripts/cluster/02-join-control-plane.sh"
echo "  worker:        sudo env PROVIDER=kakao bash scripts/cluster/02-join-worker.sh"
