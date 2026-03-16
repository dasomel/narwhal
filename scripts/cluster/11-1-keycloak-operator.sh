#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# 11a-keycloak-operator.sh
# Phase: Keycloak Operator 설치, Keycloak CR 생성, Pod Ready 대기, HTTPRoute 생성
# Depends on: 08-platform-apps.sh (Traefik), 07-cnpg.sh (PostgreSQL)

KEYCLOAK_VERSION="${KEYCLOAK_VERSION:-26.5.3}"
KEYCLOAK_ADMIN_USER="${KEYCLOAK_ADMIN_USER:-admin}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-$(generate_password)}"

export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=== Installing Keycloak ${KEYCLOAK_VERSION} with Operator ==="

# Wait for unified PostgreSQL cluster to be ready
echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true
kubectl wait --for=condition=Ready pod -l cnpg.io/cluster=narwhal-db -n database --timeout=120s || true

#=========================================
# Install Keycloak Operator
#=========================================
echo "Installing Keycloak Operator..."
kubectl apply -f "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloaks.k8s.keycloak.org-v1.yml"
kubectl apply -f "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloakrealmimports.k8s.keycloak.org-v1.yml"
kubectl apply -f "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/kubernetes.yml" -n iam

# Wait for operator
echo "Waiting for Keycloak Operator..."
kubectl wait --for=condition=Available deployment/keycloak-operator -n iam --timeout=300s || sleep 30

#=========================================
# Create admin credentials secret
#=========================================
kubectl create secret generic keycloak-admin-secret -n iam \
  --from-literal=username="${KEYCLOAK_ADMIN_USER}" \
  --from-literal=password="${KEYCLOAK_ADMIN_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Create database credentials secret (pointing to unified narwhal-db)
# Read actual Keycloak DB password from narwhal-db-credentials (created by 07-cnpg.sh)
KEYCLOAK_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
  -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo "")
kubectl create secret generic keycloak-db-secret -n iam \
  --from-literal=username=keycloak \
  --from-literal=password="${KEYCLOAK_DB_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

#=========================================
# Deploy Keycloak CR
# Note: Initial admin credentials are auto-generated and stored in {cr-name}-initial-admin secret
# DB host uses ExternalName service keycloak-db-rw → narwhal-db-rw.database.svc.cluster.local
#=========================================
echo "Creating Keycloak instance..."
cat <<EOF | kubectl apply -f -
apiVersion: k8s.keycloak.org/v2alpha1
kind: Keycloak
metadata:
  name: keycloak
  namespace: iam
spec:
  instances: 1
  db:
    vendor: postgres
    host: keycloak-db-rw
    port: 5432
    database: keycloak
    usernameSecret:
      name: keycloak-db-secret
      key: username
    passwordSecret:
      name: keycloak-db-secret
      key: password
  http:
    httpEnabled: true
  hostname:
    hostname: keycloak.local.narwhal.io
    strict: true
  proxy:
    headers: xforwarded
  # Opt-out from Istio ambient mesh — ztunnel HBONE breaks Traefik→Keycloak connectivity
  unsupported:
    podTemplate:
      metadata:
        labels:
          istio.io/dataplane-mode: "none"
  # NOTE: Do NOT use additionalOptions hostname-url (v1 deprecated in Keycloak 26.x)
  # hostname v2 with strict: true + proxy.headers: xforwarded ensures HTTPS issuer
  # when accessed via Traefik (X-Forwarded-Proto: https)
EOF

# Wait for Keycloak pods
echo "Waiting for Keycloak pods..."
sleep 30
kubectl wait --for=condition=Ready pod -l app=keycloak -n iam --timeout=600s || true

#=========================================
# Patch NetworkPolicy for Istio ambient mesh (HBONE port 15008)
#=========================================
# Keycloak Operator manages 'keycloak-network-policy' and may overwrite direct edits.
# Add a separate NetworkPolicy to allow HBONE (port 15008) used by Istio ambient mesh
# for mesh-to-mesh mTLS traffic. Without this, iam-namespace pods cannot reach keycloak.
# See CLAUDE.md Mistakes Log: "Istio ambient HBONE port 15008 NetworkPolicy blocking"
echo "Adding NetworkPolicy for Istio ambient mesh HBONE port 15008..."
cat <<NP_EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: keycloak-allow-hbone
  namespace: iam
spec:
  podSelector:
    matchLabels:
      app: keycloak
      app.kubernetes.io/instance: keycloak
      app.kubernetes.io/managed-by: keycloak-operator
  policyTypes:
  - Ingress
  ingress:
  - ports:
    - port: 15008
      protocol: TCP
NP_EOF

#=========================================
# Create NodePort Service for API Server OIDC
#=========================================
echo "=== Creating Keycloak NodePort Service ==="
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: keycloak-nodeport
  namespace: iam
spec:
  type: NodePort
  selector:
    app: keycloak
  ports:
  - port: 8080
    targetPort: 8080
    nodePort: 30080
EOF

#=========================================
# Create Keycloak ApisixRoute for HTTPS access
#=========================================
# ApisixRoute must exist BEFORE OIDC verification, otherwise curl to
# https://keycloak.local.narwhal.io fails (APISIX has no route to Keycloak).
# This is also deployed via GitOps later, but we need it now for API server OIDC setup.
# Apply Keycloak ApisixRoute (APISIX replaces Traefik Gateway — no HTTPRoute needed)
echo "Applying Keycloak ApisixRoute..."
kubectl apply -f - << 'ROUTE_EOF'
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: keycloak
  namespace: platform-system
spec:
  http:
    - name: keycloak
      match:
        hosts:
          - keycloak.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: keycloak-service
          servicePort: 8080
          serviceNamespace: iam
          resolveGranularity: service
      plugins:
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
ROUTE_EOF

# Wait for route to become effective
sleep 5

echo "=== [11a-keycloak-operator.sh] 완료 ==="
