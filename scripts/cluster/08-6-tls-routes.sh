#!/bin/bash
set -euo pipefail

echo "=== Applying TLS Routes (CA cert distribution, Traefik Gateway Routes) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Apply Traefik Gateway Routes
#=========================================
echo "=== Applying Traefik Gateway Routes ==="

# Wait for Traefik to be ready (may take time during initial provisioning)
echo "Waiting for Traefik deployment..."
for attempt in $(seq 1 12); do
  if kubectl get deployment traefik -n platform-system >/dev/null 2>&1; then
    kubectl wait --for=condition=Available deployment/traefik -n platform-system --timeout=60s 2>/dev/null && break
  fi
  echo "Traefik not ready yet, attempt ${attempt}/12..."
  sleep 15
done

# Wait for GatewayClass to be created by Traefik
echo "Waiting for Traefik GatewayClass..."
for attempt in $(seq 1 10); do
  if kubectl get gatewayclass traefik >/dev/null 2>&1; then
    echo "Traefik GatewayClass ready"
    break
  fi
  echo "GatewayClass not ready, attempt ${attempt}/10..."
  sleep 10
done

kubectl apply -f /home/vagrant/configs/gitops/resources/traefik-routes.yaml || true

# Wait for TLS certificate to be ready
echo "Waiting for TLS certificate..."
for attempt in $(seq 1 10); do
  CERT_READY=$(kubectl get certificate traefik-tls -n platform-system -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
  if [ "${CERT_READY}" = "True" ]; then
    echo "TLS certificate ready"
    break
  fi
  echo "TLS certificate not ready yet, attempt ${attempt}/10..."
  sleep 10
done

#=========================================
# Distribute CA cert to SSO app namespaces
#=========================================
echo "=== Distributing CA cert to SSO namespaces ==="
CA_CERT=$(kubectl get secret traefik-tls -n platform-system -o jsonpath='{.data.ca\.crt}' 2>/dev/null || echo "")
if [ -n "${CA_CERT}" ]; then
  for ns in devtools iam monitoring storage; do
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f - 2>/dev/null
    kubectl create secret generic narwhal-ca-cert \
      --from-literal=ca.crt="$(echo "${CA_CERT}" | base64 -d)" \
      -n "${ns}" --dry-run=client -o yaml | kubectl apply -f -
  done
  echo "CA cert distributed to SSO namespaces"
else
  echo "WARN: traefik-tls CA cert not found, SSO apps may not verify Keycloak TLS"
fi

echo "=== TLS Routes Installation Complete ==="
