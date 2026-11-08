#!/bin/bash
set -euo pipefail

echo "=== Applying TLS Routes (CA cert distribution, APISIX Routes) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

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

echo "=== TLS Routes and APISIX Routes Installation Complete ==="
