#!/bin/bash
set -euo pipefail

ARGOCD_VERSION="${ARGOCD_VERSION:-v3.4.4}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"

# D-authmig: Keycloak OIDC (Authentik removed)
KEYCLOAK_ISSUER="https://keycloak.${DOMAIN}/realms/narwhal"

echo "=== Installing ArgoCD ${ARGOCD_VERSION} ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

# Wait for API server to be reachable (may restart under memory pressure)
echo "Waiting for API server..."
for i in {1..30}; do
  if kubectl get nodes &>/dev/null; then
    break
  fi
  echo "API server not ready, retrying... (${i}/30)"
  sleep 10
done

# Create namespace with Istio ambient mesh label
kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace devtools istio.io/dataplane-mode=ambient --overwrite

# Install ArgoCD (server-side apply for large CRDs like applicationsets)
kubectl apply -n devtools -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml" \
  --server-side --force-conflicts

# Fix ClusterRoleBindings: ArgoCD is installed in devtools, not argocd namespace
# The upstream manifests always use 'argocd' as Subject namespace - must be patched
echo "Fixing ClusterRoleBinding Subject namespaces (argocd -> devtools)..."
for crb in argocd-application-controller argocd-applicationset-controller argocd-server; do
  kubectl patch clusterrolebinding "${crb}" --type='json' \
    -p='[{"op": "replace", "path": "/subjects/0/namespace", "value": "devtools"}]' 2>/dev/null || true
done

# Patch ArgoCD NetworkPolicies for Istio ambient mesh (HBONE port 15008)
echo "Patching ArgoCD NetworkPolicies for Istio ambient mesh (HBONE port 15008)..."
# NetworkPolicy 생성 대기 (최대 30초)
for i in $(seq 1 30); do
  if kubectl get networkpolicy -n devtools 2>/dev/null | grep -q argocd; then
    break
  fi
  sleep 1
done
for np in $(kubectl get networkpolicy -n devtools -o name 2>/dev/null | grep argocd); do
  kubectl patch "$np" -n devtools --type='json' \
    -p='[{"op": "add", "path": "/spec/ingress/0/ports/-", "value": {"port": 15008, "protocol": "TCP"}}]' 2>/dev/null || true
done

# Fix Istio ambient mesh + kubelet health probe conflict
# In ambient mode, ztunnel intercepts ALL inbound pod traffic including kubelet probes.
# Kubelet sends plain HTTP probes; ztunnel expects mTLS -> probe times out -> CrashLoopBackOff.
# Fix: opt these pods out of ambient (they communicate via plaintext internally)
#   - argocd-repo-server: liveness/readiness on port 8084 (HTTP)
#   - argocd-notifications-controller: liveness probe on port 9001 (TCP)
#   - argocd-application-controller: readiness on port 8082 (HTTP)
echo "Opting ArgoCD pods out of Istio ambient mesh..."

# argocd-server: opt out of ambient (SSO cookie flow breaks under ztunnel interception)
kubectl patch deployment argocd-server -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'

# repo-server: opt out of ambient (istio.io/dataplane-mode=none on pod label)
kubectl patch deployment argocd-repo-server -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'

# notifications-controller: opt out of ambient
kubectl patch deployment argocd-notifications-controller -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'

# application-controller (StatefulSet): opt out of ambient
kubectl patch statefulset argocd-application-controller -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'
# StatefulSet does not auto-rollout on label change; delete pod to trigger recreation
kubectl delete pod argocd-application-controller-0 -n devtools 2>/dev/null || true

# Wait for ArgoCD pods
echo "Waiting for ArgoCD pods..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n devtools --timeout=300s || true
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-repo-server -n devtools --timeout=300s || true
kubectl wait --for=condition=Ready pod/argocd-application-controller-0 -n devtools --timeout=180s || true

# Configure ArgoCD server params (v3.x reads from cmd-params-cm, not argocd-cm)
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-cmd-params-cm
  namespace: devtools
  labels:
    app.kubernetes.io/name: argocd-cmd-params-cm
    app.kubernetes.io/part-of: argocd
data:
  server.insecure: "true"
EOF

# D-authmig: Configure ArgoCD for Keycloak OIDC
# argocd-oidc-secret (devtools) created by 11-3-keycloak-clients.sh with keycloak client 'argocd'
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-cm
  namespace: devtools
  labels:
    app.kubernetes.io/name: argocd-cm
    app.kubernetes.io/part-of: argocd
data:
  url: https://argocd.${DOMAIN}
  server.insecure: "true"
  oidc.tls.insecure.skip.verify: "true"
  oidc.config: |
    name: Keycloak
    issuer: ${KEYCLOAK_ISSUER}
    clientID: argocd
    clientSecret: \$oidc.keycloak.clientSecret
    requestedScopes:
      - openid
      - profile
      - email
      - groups
    insecureSkipVerify: true
EOF

# Configure RBAC for Keycloak groups
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-rbac-cm
  namespace: devtools
  labels:
    app.kubernetes.io/name: argocd-rbac-cm
    app.kubernetes.io/part-of: argocd
data:
  policy.default: role:readonly
  policy.csv: |
    p, role:developer, applications, sync, */*, allow
    p, role:developer, applications, get, */*, allow
    p, role:developer, logs, get, */*, allow
    p, role:none, applications, get, */*, deny
    g, cluster-admin, role:admin
    g, developer, role:developer
    g, viewer, role:readonly
    g, guest, role:none
  scopes: '[groups]'
EOF

# D-authmig: Inject Keycloak OIDC client secret into argocd-secret
# argocd-oidc-secret (devtools) is created by 11-3-keycloak-clients.sh with client 'argocd'
ARGOCD_CLIENT_SECRET=$(kubectl get secret argocd-oidc-secret -n devtools \
  -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d || echo "")
if [ -n "${ARGOCD_CLIENT_SECRET}" ]; then
  kubectl patch secret argocd-secret -n devtools --type=merge \
    -p "{\"stringData\":{\"oidc.keycloak.clientSecret\":\"${ARGOCD_CLIENT_SECRET}\"}}"
  echo "argocd-secret patched with oidc.keycloak.clientSecret"
else
  echo "WARN: argocd-oidc-secret not found in devtools — run 11-3-keycloak-clients.sh first"
fi

# Restart ArgoCD server to apply OIDC config
kubectl rollout restart deployment argocd-server -n devtools
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n devtools --timeout=300s || true

# argocd-redis에 PING liveness/readiness probe 추가 (업스트림 manifest에는 probe 없음).
# 호스트 과부하 시 redis가 TCP는 살아 있지만 EOF를 뱉는 웨지 상태가 되면
# repo-server 캐시가 전부 실패해 모든 앱 SYNC=Unknown이 된다(2026-07-10 3회 재발).
# 실제 Redis 프로토콜(PING)로 점검해야 웨지를 감지하고 자동 재시작된다.
# shellcheck disable=SC2016  # $REDIS_PASSWORD/$(...)는 컨테이너 내부 sh에서 확장 (의도적 single quote)
kubectl patch deployment argocd-redis -n devtools --type strategic -p '{
  "spec": {"template": {"spec": {"containers": [{
    "name": "redis",
    "livenessProbe": {
      "exec": {"command": ["sh", "-c", "response=$(redis-cli -a \"$REDIS_PASSWORD\" --no-auth-warning ping) && [ \"$response\" = \"PONG\" ]"]},
      "initialDelaySeconds": 10, "periodSeconds": 15, "timeoutSeconds": 5, "failureThreshold": 3
    },
    "readinessProbe": {
      "exec": {"command": ["sh", "-c", "response=$(redis-cli -a \"$REDIS_PASSWORD\" --no-auth-warning ping) && [ \"$response\" = \"PONG\" ]"]},
      "initialDelaySeconds": 5, "periodSeconds": 10, "timeoutSeconds": 5, "failureThreshold": 2
    }
  }]}}}
}' || true
kubectl rollout status deployment argocd-redis -n devtools --timeout=180s || true

# Get initial admin password
ARGOCD_PASSWORD=$(kubectl -n devtools get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d 2>/dev/null || echo "unknown")

echo "=== ArgoCD Installation Done ==="

echo ""
echo "=========================================="
echo "ArgoCD Ready!"
echo "=========================================="
echo ""
echo "Access:"
echo "  kubectl port-forward svc/argocd-server -n devtools 8443:443"
echo "  URL: https://localhost:8443"
echo "  User: admin"
echo "  Password: ${ARGOCD_PASSWORD}"
echo ""
echo "OIDC Login: Use Keycloak credentials (realm: narwhal)"
echo ""
kubectl get pods -n devtools
