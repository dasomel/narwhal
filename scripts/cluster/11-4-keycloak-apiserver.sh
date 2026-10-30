#!/bin/bash
set -euo pipefail

# 11-4-keycloak-apiserver.sh
# Phase: K8s API 서버 OIDC 설정, master-2/master-3 SSH 전파, ClusterRoleBindings 생성 (RBAC)
# Depends on: 11-2-keycloak-config.sh (Keycloak OIDC endpoint must be ready)

export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=== Configuring Kubernetes API Server for OIDC and RBAC ==="

#=========================================
# Gate on the API server before the first kubectl.
#
# 2026-08-04: this script died on its very first command —
#   kubectl create namespace dev ... | kubectl apply -f -
#   error validating "STDIN": failed to download openapi:
#     dial tcp 192.168.56.10:6443: connect: connection refused
# and being `critical` it took the whole of Phase 2 down with it. The API server was
# unavailable because kubelet had just (re)started at 19:39:20 and was still bringing the
# static pods back; boot-heal was NOT responsible (it logged "No wedge detected" through
# the entire window) and no `vagrant reload master-1` appears in the run log, so what
# triggered that restart is still unidentified — vagrant's output carries no timestamps to
# correlate against.
#
# The gate is worth having regardless of the trigger: an unavailable API server during
# Phase 2 is a wait, not a failure, and 07-cnpg.sh already opens with the same loop.
# Without it, any control-plane blip in this window aborts the install.
#=========================================
echo "Waiting for API server..."
for i in {1..30}; do
  if kubectl get --raw /readyz &>/dev/null; then
    break
  fi
  echo "API server not ready, retrying... (${i}/30)"
  sleep 10
done
kubectl get --raw /readyz >/dev/null || {
  echo "ERROR: API server still unreachable after 5 minutes; not touching its manifest." >&2
  exit 1
}

#=========================================
# Create Kubernetes RBAC for OIDC groups
#=========================================
echo "=== Creating Kubernetes RBAC for OIDC ==="

# Create dev namespace for developer workloads
kubectl create namespace dev --dry-run=client -o yaml | kubectl apply -f -

# ClusterRoleBinding for cluster-admin group → platform-admin ClusterRole
# NOTE: platform-admin ClusterRole is defined in gitops/resources/rbac-policies.yaml
# and deployed by ArgoCD at step 14. Creating CRB here with platform-admin reference
# ensures no roleRef conflict when ArgoCD syncs rbac-policies.yaml.
# roleRef is immutable — delete existing CRB first to handle any prior cluster-admin binding.
kubectl delete clusterrolebinding oidc-cluster-admin --ignore-not-found
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-cluster-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: platform-admin
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:cluster-admin
EOF

# NOTE: dev ns의 edit 바인딩은 gitops/resources/rbac-policies.yaml의
# developer-full-access(RoleBinding)가 소유한다 — 여기서 중복 생성하지 않음.

# RoleBinding: developer group gets view in devtools and monitoring
for ns in devtools monitoring; do
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-developer-view
  namespace: ${ns}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developer
EOF
done

# RoleBinding: viewer group gets view in dev, devtools, and monitoring
for ns in dev devtools monitoring; do
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-viewer-view
  namespace: ${ns}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:viewer
EOF
done

# guest group: no K8s RBAC (web UI OIDC only)

#=========================================
# Configure API Server for OIDC
#=========================================
echo "=== Configuring API Server for OIDC ==="

DOMAIN="${DOMAIN:-local.narwhal.internal}"
# K8s 1.35+ requires HTTPS for --oidc-issuer-url; HTTP causes API server crash
OIDC_ISSUER_URL="https://keycloak.${DOMAIN}/realms/narwhal"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_OIDC_SCRIPT="${SCRIPT_DIR}/11-4b-apiserver-oidc-node.sh"
if [ ! -f "${NODE_OIDC_SCRIPT}" ]; then
  NODE_OIDC_SCRIPT="/home/vagrant/scripts/cluster/11-4b-apiserver-oidc-node.sh"
fi
KUBEADM_CONFIG_OIDC_SCRIPT="${SCRIPT_DIR}/11-4c-kubeadm-config-oidc.sh"
if [ ! -f "${KUBEADM_CONFIG_OIDC_SCRIPT}" ]; then
  KUBEADM_CONFIG_OIDC_SCRIPT="/home/vagrant/scripts/cluster/11-4c-kubeadm-config-oidc.sh"
fi

# Verify HTTPS OIDC endpoint is reachable before activating
echo "Verifying OIDC issuer HTTPS endpoint..."
OIDC_REACHABLE=false
for attempt in {1..15}; do
  HTTP_CODE=$(curl -sk -o /dev/null -w '%{http_code}' "${OIDC_ISSUER_URL}/.well-known/openid-configuration" 2>/dev/null) || HTTP_CODE="000"
  if [ "${HTTP_CODE}" = "200" ]; then
    OIDC_REACHABLE=true
    echo "OIDC endpoint reachable (HTTP ${HTTP_CODE})"
    break
  fi
  echo "OIDC endpoint not ready (HTTP ${HTTP_CODE}), attempt ${attempt}/15..."
  sleep 10
done

if [ "${OIDC_REACHABLE}" = "false" ]; then
  echo "ERROR: OIDC HTTPS endpoint not reachable (${OIDC_ISSUER_URL}/.well-known/openid-configuration)." >&2
  echo "  Possible causes:" >&2
  echo "    - cert-manager TLS certificate not issued yet" >&2
  echo "    - APISIX Gateway not routing keycloak.${DOMAIN}" >&2
  echo "    - DNS not resolving keycloak.${DOMAIN}" >&2
  echo "  Run scripts in order: 08-1-networking.sh..08-6-tls-routes.sh → 10-dnsmasq.sh → 11-keycloak.sh → 11-2-keycloak-config.sh" >&2
  exit 1
fi

# D-APISERVER-OIDC-ALL: extract the Keycloak TLS CA to /tmp/oidc-ca.crt, apply OIDC CA + the
# 7 --oidc-* flags to master-1 via 11-4b-apiserver-oidc-node.sh, and then propagate to the
# other masters (via sshpass on Vagrant, or from the operator host via
# scripts/cloud/apply-apiserver-oidc-kakao.sh on Kakao where nodes have no vagrant password
# and no node-to-node trust). Any master failure aborts the run.
# Reason: all control-plane nodes sit behind the in-cluster `kubernetes` Service; a swallowed
# master-2/3 failure left 2/3 of apiservers rejecting OIDC bearer tokens with 401.
# Cost: master-2/3 unreachable or unhealthy during 11-4 now fails Phase 2 immediately. So does an
# unreachable issuer: the endpoint check above used to warn and skip the OIDC activation and now
# exits 1, and 11-4 is a CRITICAL_SCRIPTS entry in 06-phase2-start.sh, so Phase 2 stops. That
# happens when the non-critical 08-6-tls-routes.sh / 10-dnsmasq.sh steps only warned, or when
# the bastion DNS is down (#478).
# Escape hatch: MASTER_COUNT=1 for a single-master topology; for an unreachable issuer, fix the
# cause listed in the error and re-run Phase 2.
echo "Extracting Keycloak TLS CA certificate from narwhal-root-ca-secret..."
if kubectl get secret -n platform-system narwhal-root-ca-secret &>/dev/null; then
  kubectl get secret -n platform-system narwhal-root-ca-secret \
    -o jsonpath='{.data.tls\.crt}' | base64 -d > /tmp/oidc-ca.crt
  echo "CA cert extracted from narwhal-root-ca-secret"
else
  echo "Fallback: extracting CA from TLS handshake..."
  openssl s_client -connect "keycloak.${DOMAIN}:443" -showcerts </dev/null 2>/dev/null \
    | awk '/-----BEGIN CERTIFICATE-----/{c=""} {c=c $0 "\n"} /-----END CERTIFICATE-----/{last=c} END{printf "%s", last}' \
    > /tmp/oidc-ca.crt
fi

echo "Applying OIDC CA + flags to master-1..."
if ! sudo bash "${NODE_OIDC_SCRIPT}" "${OIDC_ISSUER_URL}" /tmp/oidc-ca.crt; then
  echo "ERROR: Failed to apply OIDC configuration to master-1. Fix the cause above and re-run: sudo bash /home/vagrant/scripts/cluster/11-4-keycloak-apiserver.sh" >&2
  exit 1
fi

# D-KUBEADM-CONFIG-OIDC (#505): the manifest edit above is lost when `kubeadm upgrade` regenerates
# the static pod, and a control plane joined later renders its manifest from kubeadm-config.
# Persist the same 7 flags in ClusterConfiguration, once per cluster (cluster-admin kubectl is
# here; the other masters need nothing). The helper writes nothing on any doubt and exits
# non-zero, which stops Phase 2 so the drift is not silently left in place.
# Cost: a failure here (unparsable or unexpected kubeadm-config) stops Phase 2 after master-1
# already runs with OIDC. Escape hatch: re-run this script after fixing the cause; the original
# ClusterConfiguration is saved to /etc/kubernetes/kubeadm-config.ClusterConfiguration.pre-oidc-*.
echo "Persisting the OIDC flags in kubeadm-config (survives kubeadm upgrade)..."
if ! sudo env KUBECONFIG="${KUBECONFIG}" bash "${KUBEADM_CONFIG_OIDC_SCRIPT}" "${OIDC_ISSUER_URL}"; then
  echo "ERROR: Failed to persist OIDC flags in kube-system/kubeadm-config. Fix the cause above and re-run: sudo bash /home/vagrant/scripts/cluster/11-4-keycloak-apiserver.sh" >&2
  exit 1
fi

if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: skipping node-to-node SSH; the operator host applies OIDC flags to the other masters via: ./scripts/cloud/apply-apiserver-oidc-kakao.sh (or ./scripts/cloud/provision-kakao.sh oidc)"
else
  # Vagrant: propagate CA + run the node script on master-2 and master-3 over sshpass.
  # Master IPs: master-2=192.168.56.11, master-3=192.168.56.12
  # UserKnownHostsFile=/dev/null is deliberate and only reachable on this disposable local-lab
  # path (PROVIDER=kakao never gets here; scripts/cloud/lib-ssh.sh pins host keys there): after
  # `vagrant destroy` a re-created master presents a new host key, and with a stale known_hosts
  # entry OpenSSH disables password authentication, so sshpass fails and Phase 2 stops.
  MASTER_IPS="${MASTER_IPS:-192.168.56.11 192.168.56.12}"
  MASTER_COUNT="${MASTER_COUNT:-3}"
  IDX=2
  for MASTER_IP in ${MASTER_IPS}; do
    if [ "${IDX}" -gt "${MASTER_COUNT}" ]; then break; fi
    echo "Applying OIDC CA + flags to master-${IDX} (${MASTER_IP})..."
    if ! sshpass -p "vagrant" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null /etc/kubernetes/pki/oidc-ca.crt \
      "vagrant@${MASTER_IP}:/tmp/oidc-ca.crt"; then
      echo "ERROR: Could not copy OIDC CA to master-${IDX} (${MASTER_IP}). Re-run: vagrant ssh master-1 -c 'sudo bash /home/vagrant/scripts/cluster/11-4-keycloak-apiserver.sh'" >&2
      exit 1
    fi
    if ! sshpass -p "vagrant" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "vagrant@${MASTER_IP}" \
      "sudo bash /home/vagrant/scripts/cluster/11-4b-apiserver-oidc-node.sh '${OIDC_ISSUER_URL}' /tmp/oidc-ca.crt"; then
      echo "ERROR: Could not apply OIDC flags to master-${IDX} (${MASTER_IP}). Re-run: vagrant ssh master-1 -c 'sudo bash /home/vagrant/scripts/cluster/11-4-keycloak-apiserver.sh'" >&2
      exit 1
    fi
    IDX=$((IDX + 1))
  done
fi

echo "=== [11-4-keycloak-apiserver.sh] 완료 ==="
