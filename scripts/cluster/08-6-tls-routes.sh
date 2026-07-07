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
helm template narwhal-platform /home/vagrant/configs/gitops/charts/narwhal-platform --set baseDomain="${DOMAIN}" --show-only templates/apisix-routes.yaml 2>/dev/null | kubectl apply -f - || true

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
  for ns in devtools iam monitoring storage; do
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f - 2>/dev/null
    kubectl create secret generic narwhal-ca-cert \
      --from-literal=ca.crt="$(printf '%s\n%s\n' "$(echo "${CA_CERT}" | base64 -d)" "${K8S_CA}")" \
      -n "${ns}" --dry-run=client -o yaml | kubectl apply -f -
  done
  echo "CA cert (narwhal + k8s) distributed to SSO namespaces"
else
  echo "WARN: narwhal-wildcard-tls CA cert not found, SSO apps may not verify Keycloak TLS"
fi

echo "=== TLS Routes and APISIX Routes Installation Complete ==="
