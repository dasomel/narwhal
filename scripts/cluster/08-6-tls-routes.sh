#!/bin/bash
set -euo pipefail

echo "=== Applying TLS Routes (CA cert distribution, APISIX Routes) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local
DOMAIN="${DOMAIN:-local.narwhal.internal}"

#=========================================
# Apply APISIX Routes
#=========================================
echo "=== Applying APISIX Routes ==="

# Apply APISIX routes (after cert is ready and APISIX is running)
echo "Applying APISIX routes..."
if ! helm template narwhal-platform /home/vagrant/configs/gitops/charts/narwhal-platform \
  --set baseDomain="${DOMAIN}" --show-only templates/apisix-routes.yaml | kubectl apply -f -; then
  echo "ERROR: could not apply the canonical APISIX routes." >&2
  echo "       11-keycloak.sh is the later critical HTTPS/OIDC gate; do not continue by applying a second route." >&2
  exit 1
fi

# The pinned ingress-controller can leave resources aborted after an initial 404 on empty etcd.
echo "Waiting for APISIX ingress controller before bounded sync recovery..."
kubectl rollout status deployment/apisix-ingress-controller -n platform-system --timeout=180s
"$(dirname "$0")/lib/recover-apisix-resource-sync.sh" platform-system

# Wait for APISIX Ingress Controller to sync routes
echo "Waiting for APISIX routes to sync..."
sleep 10
kubectl get apisixroute -n platform-system || true

# Wait for TLS certificate to be ready
echo "Waiting for TLS certificate..."
cert_ready="false"
for attempt in $(seq 1 10); do
  CERT_READY=$(kubectl get certificate narwhal-wildcard-tls -n platform-system -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
  if [ "${CERT_READY}" = "True" ]; then
    echo "TLS certificate ready"
    cert_ready="true"
    break
  fi
  echo "TLS certificate not ready yet, attempt ${attempt}/10..."
  sleep 10
done
if [[ "${cert_ready}" != "true" ]]; then
  echo "WARN: TLS certificate not ready after timeout, routes may not work properly"
  # continue anyway - cert-manager will eventually provision it
fi

#=========================================
# Distribute CA cert to SSO app namespaces
#=========================================
echo "=== Distributing CA cert to SSO namespaces ==="
CA_CERT=$(kubectl get secret narwhal-wildcard-tls -n platform-system -o jsonpath='{.data.ca\.crt}' 2>/dev/null || echo "")
# Also bundle the Kubernetes cluster CA so consumers can verify the kube-apiserver over
# TLS. narwhal-portal talks to the API server (https://VIP:6443, cert signed by CN=kubernetes)
# via NODE_EXTRA_CA_CERTS=/etc/ssl/narwhal/ca.crt — the narwhal private CA alone yields
# UNABLE_TO_VERIFY_LEAF_SIGNATURE and blanks the cluster-infra page. Appending the k8s CA is
# additive (other consumers still trust the narwhal CA for Keycloak/etc).
K8S_CA=$(cat /etc/kubernetes/pki/ca.crt 2>/dev/null || echo "")
if [ -n "${CA_CERT}" ]; then
  # D-HARBOR-CA-ROTATE (#551): harbor-core reads caBundleSecretName only at start, so a rotated CA
  # leaves Harbor OIDC login failing closed until it restarts. GitOps owns the harbor Deployment but
  # the Secret is made here, so a checksum annotation is not available; compare the Secret before and
  # after the apply and roll harbor-core only when ca.crt really changed. Cost: one core restart per
  # rotation. Escape hatch: kubectl -n devtools rollout restart deployment/harbor-core by hand.
  CA_BEFORE=$(kubectl get secret narwhal-ca-cert -n devtools -o jsonpath='{.data.ca\.crt}' 2>/dev/null || true)
  # security-system: the trivy-db-refresh CronJob verifies Harbor's TLS with it (D-SECDB-AUTO, #585).
  for ns in devtools iam monitoring storage security-system; do
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f - 2>/dev/null
    kubectl create secret generic narwhal-ca-cert \
      --from-literal=ca.crt="$(printf '%s\n%s\n' "$(echo "${CA_CERT}" | base64 -d)" "${K8S_CA}")" \
      -n "${ns}" --dry-run=client -o yaml | kubectl apply -f -
  done
  echo "CA cert (narwhal + k8s) distributed to SSO namespaces"
  CA_AFTER=$(kubectl get secret narwhal-ca-cert -n devtools -o jsonpath='{.data.ca\.crt}' 2>/dev/null || true)
  # A first install has no previous Secret (CA_BEFORE empty) and harbor-core starts with the new one.
  if [ -n "${CA_BEFORE}" ] && [ "${CA_BEFORE}" != "${CA_AFTER}" ] \
    && kubectl get deployment harbor-core -n devtools >/dev/null 2>&1; then
    echo "narwhal-ca-cert changed; restarting harbor-core so it reloads the CA bundle..."
    if kubectl rollout restart deployment/harbor-core -n devtools \
      && kubectl rollout status deployment/harbor-core -n devtools --timeout=180s; then
      echo "harbor-core restarted"
    else
      echo "WARN: harbor-core restart did not finish; run: kubectl -n devtools rollout restart deployment/harbor-core" >&2
    fi
  fi
else
  echo "WARN: narwhal-wildcard-tls CA cert not found, SSO apps may not verify Keycloak TLS"
fi

#=========================================
# Configure containerd TLS trust for Harbor (KISA-IMG-01, D-HARBOR-NODE-CA)
#=========================================
# Installs the Narwhal Root CA into /etc/containerd/certs.d/harbor.${DOMAIN}/ca.crt and
# writes hosts.toml with `ca = ...` (removing any legacy skip_verify = true). Runs on
# initial bring-up and on every CA rotation so nodes stay in sync with harbor-core.
echo "=== Configuring containerd TLS trust for harbor.${DOMAIN} ==="
HARBOR_NODE_TLS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/08-6b-harbor-containerd-tls-node.sh"
if [ ! -f "${HARBOR_NODE_TLS_SCRIPT}" ]; then
  HARBOR_NODE_TLS_SCRIPT="/home/vagrant/scripts/cluster/08-6b-harbor-containerd-tls-node.sh"
fi
if [ -z "${CA_CERT}" ]; then
  echo "ERROR: narwhal-wildcard-tls CA cert is empty; cannot configure containerd TLS verification for harbor.${DOMAIN} (refusing skip_verify fallback)." >&2
  exit 1
fi
if [ ! -f "${HARBOR_NODE_TLS_SCRIPT}" ]; then
  echo "ERROR: ${HARBOR_NODE_TLS_SCRIPT} not found; cannot configure containerd TLS trust for harbor.${DOMAIN}." >&2
  exit 1
fi

HARBOR_CA_TMP="$(mktemp /tmp/narwhal-harbor-ca.XXXXXX)"
trap 'rm -f "${HARBOR_CA_TMP}"' EXIT
chmod 0600 "${HARBOR_CA_TMP}"
printf '%s' "${CA_CERT}" | base64 -d > "${HARBOR_CA_TMP}"

echo "Applying Harbor CA to local node (master-1)..."
if ! sudo env DOMAIN="${DOMAIN}" bash "${HARBOR_NODE_TLS_SCRIPT}" "${HARBOR_CA_TMP}" "${DOMAIN}"; then
  echo "ERROR: failed to configure containerd Harbor TLS on local node." >&2
  exit 1
fi

if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: skipping node-to-node SSH; propagate Harbor CA to remaining nodes from the operator host via: ./scripts/cloud/apply-harbor-ca-kakao.sh"
else
  # Propagate to all other cluster nodes (masters and workers) over Vagrant sshpass, matching
  # 11-4-keycloak-apiserver.sh's local-lab channel. Fail loudly if any node fails so no node
  # silently keeps skip_verify = true or a stale CA after rotation.
  LOCAL_IPS=" $(hostname -I 2>/dev/null || true) "
  CLUSTER_NODE_IPS="$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}' 2>/dev/null || true)"
  HARBOR_PROP_FAILED=0
  while IFS= read -r node_ip; do
    [ -n "${node_ip}" ] || continue
    case "${LOCAL_IPS}" in
      *" ${node_ip} "*) continue ;;
    esac
    echo "Propagating Harbor CA to node ${node_ip}..."
    if ! sshpass -p "vagrant" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "${HARBOR_CA_TMP}" "vagrant@${node_ip}:/tmp/harbor-ca.crt"; then
      echo "ERROR: could not copy Harbor CA to ${node_ip}" >&2
      HARBOR_PROP_FAILED=$((HARBOR_PROP_FAILED + 1))
      continue
    fi
    if ! sshpass -p "vagrant" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "vagrant@${node_ip}" \
      "trap 'rm -f /tmp/harbor-ca.crt' EXIT; sudo env DOMAIN='${DOMAIN}' bash /home/vagrant/scripts/cluster/08-6b-harbor-containerd-tls-node.sh /tmp/harbor-ca.crt '${DOMAIN}'"; then
      echo "ERROR: could not configure containerd Harbor TLS on ${node_ip}" >&2
      HARBOR_PROP_FAILED=$((HARBOR_PROP_FAILED + 1))
    fi
  done <<< "${CLUSTER_NODE_IPS}"
  if [ "${HARBOR_PROP_FAILED}" -gt 0 ]; then
    echo "ERROR: Harbor containerd CA propagation failed on ${HARBOR_PROP_FAILED} node(s). Fix SSH/node health and re-run 08-6-tls-routes.sh." >&2
    exit 1
  fi
fi

rm -f "${HARBOR_CA_TMP}"
trap - EXIT

echo "=== TLS Routes and APISIX Routes Installation Complete ==="
