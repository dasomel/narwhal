#!/bin/bash
set -euo pipefail
# shellcheck source=scripts/common/lib.sh
source /home/vagrant/scripts/common/lib.sh

GITEA_VERSION="${GITEA_VERSION:-v1.26.2}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"

echo "=== Installing Gitea ${GITEA_VERSION} ==="

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

# Create namespace
kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -

# Add Gitea Helm repo
helm repo add gitea-charts https://dl.gitea.com/charts/
helm repo update

# Wait for unified PostgreSQL cluster (narwhal-db) to be ready
echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true
kubectl wait --for=condition=Ready pod -l cnpg.io/poolerName=narwhal-db-pooler-rw -n database --timeout=120s || true

# D-authmig: Keycloak OIDC (Authentik removed)
KEYCLOAK_URL="https://keycloak.${DOMAIN:-local.narwhal.internal}"
KEYCLOAK_REALM="narwhal"

# Gitea admin password — create Secret on first run, reuse on re-run
if ! kubectl get secret gitea-admin -n devtools &>/dev/null; then
  GITEA_ADMIN_PASS=$(generate_password)
  kubectl create secret generic gitea-admin \
    --from-literal=admin-password="${GITEA_ADMIN_PASS}" \
    -n devtools
else
  GITEA_ADMIN_PASS=$(kubectl get secret gitea-admin -n devtools \
    -o jsonpath='{.data.admin-password}' | base64 -d)
fi

# DB password — provided by 07-cnpg.sh via narwhal-db-credentials Secret
GITEA_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
  -o jsonpath='{.data.gitea-password}' | base64 -d)

# Install Gitea with Keycloak OIDC
#
# Cache/session/queue backend: the gitea chart bundles a Bitnami Valkey subchart
# (image docker.io/bitnamilegacy/valkey) when valkey.enabled=true, which violates
# this repo's Bitnami ban AND is unnecessary here — gitea runs as a SINGLE replica,
# so it has no need for a shared external cache. With valkey disabled the chart
# leaves gitea on its built-in defaults: memory cache, memory session, and a
# level (leveldb) queue persisted on the gitea PVC. Trade-off: in-memory sessions
# drop on a gitea pod restart (users re-login) — acceptable for a dev IDP git
# server. Re-enable valkey (with a non-Bitnami image) only if gitea goes HA.
GITEA_CHART_VERSION="${GITEA_CHART_VERSION:-12.6.0}"

helm upgrade --install gitea gitea-charts/gitea \
  --namespace devtools \
  --version "${GITEA_CHART_VERSION}" \
  --set image.tag="${GITEA_VERSION#v}" \
  --set gitea.admin.username=gitea-admin \
  --set gitea.admin.password="${GITEA_ADMIN_PASS}" \
  --set gitea.admin.email=admin@local \
  --set postgresql.enabled=false \
  --set postgresql-ha.enabled=false \
  --set gitea.config.database.DB_TYPE=postgres \
  --set gitea.config.database.HOST=gitea-db-rw:5432 \
  --set gitea.config.database.NAME=gitea \
  --set gitea.config.database.USER=gitea \
  --set gitea.config.database.PASSWD="${GITEA_DB_PASS}" \
  --set gitea.config.server.ROOT_URL=https://gitea.${DOMAIN} \
  --set gitea.config.oauth2_client.ENABLE_AUTO_REGISTRATION=true \
  --set gitea.config.oauth2_client.ACCOUNT_LINKING=auto \
  --set gitea.config.oauth2_client.UPDATE_AVATAR=true \
  --set gitea.config.oauth2_client.OPENID_CONNECT_SCOPES="openid profile email groups" \
  --set gitea.config.service.ENABLE_REVERSE_PROXY_AUTHENTICATION=true \
  --set gitea.config.service.ENABLE_REVERSE_PROXY_AUTO_REGISTRATION=true \
  --set gitea.config.security.REVERSE_PROXY_AUTHENTICATION_USER=X-WEBAUTH-USER \
  --set gitea.config.security.REVERSE_PROXY_TRUSTED_PROXIES="*" \
  --set persistence.enabled=true \
  --set persistence.storageClass=nfs-csi \
  --set persistence.size=10Gi \
  --set redis-cluster.enabled=false \
  --set redis.enabled=false \
  --set valkey-cluster.enabled=false \
  --set valkey.enabled=false \
  --set "extraVolumes[0].name=narwhal-ca" \
  --set "extraVolumes[0].secret.secretName=narwhal-ca-cert" \
  --set "extraContainerVolumeMounts[0].name=narwhal-ca" \
  --set "extraContainerVolumeMounts[0].mountPath=/etc/ssl/certs/narwhal-ca.crt" \
  --set "extraContainerVolumeMounts[0].subPath=ca.crt" \
  --set "extraContainerVolumeMounts[0].readOnly=true" \
  --set containerSecurityContext.allowPrivilegeEscalation=false \
  --set containerSecurityContext.capabilities.drop[0]=ALL \
  --set containerSecurityContext.seccompProfile.type=RuntimeDefault \
  --timeout=600s || echo "WARN: Gitea install timed out, continuing..."

# Opt Gitea out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment gitea -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

# Configure Keycloak OAuth2 provider via API
echo "Configuring Gitea OAuth2 provider..."
sleep 10

# Wait for Gitea to be ready
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea -n devtools --timeout=300s || true

# Create OAuth2 source via Gitea API
GITEA_POD=$(kubectl get pod -n devtools -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "${GITEA_POD}" ]; then
  # Create narwhal organization for group→team mapping
  echo "Creating narwhal organization..."
  kubectl exec -n devtools "${GITEA_POD}" -- \
    curl -sf -X POST "http://localhost:3000/api/v1/orgs" \
      -H "Content-Type: application/json" \
      -u "gitea-admin:${GITEA_ADMIN_PASS}" \
      -d '{"username":"narwhal","full_name":"Narwhal","visibility":"public"}' 2>/dev/null || true

  # Create teams in narwhal org
  for team_data in '{"name":"Developers","permission":"write","units":["repo.code","repo.issues","repo.pulls"]}' \
                   '{"name":"Viewers","permission":"read","units":["repo.code","repo.issues"]}'; do
    kubectl exec -n devtools "${GITEA_POD}" -- \
      curl -sf -X POST "http://localhost:3000/api/v1/orgs/narwhal/teams" \
        -H "Content-Type: application/json" \
        -u "gitea-admin:${GITEA_ADMIN_PASS}" \
        -d "${team_data}" 2>/dev/null || true
  done

  # D-authmig: Configure Keycloak OAuth2 source (replaced Authentik)
  # gitea-oidc-secret created by 11-3-keycloak-clients.sh (keycloak client: gitea)
  GITEA_CLIENT_SECRET=$(kubectl get secret gitea-oidc-secret -n devtools \
    -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d || echo "")

  if [ -z "${GITEA_CLIENT_SECRET}" ]; then
    echo "WARN: gitea-oidc-secret not found in devtools — run 11-3-keycloak-clients.sh first"
  else
    # Idempotent: remove existing source before re-adding
    kubectl exec -n devtools "${GITEA_POD}" -- \
      /app/gitea/gitea admin auth delete --name keycloak 2>/dev/null || true

    kubectl exec -n devtools "${GITEA_POD}" -- \
      /app/gitea/gitea admin auth add-oauth \
        --name keycloak \
        --provider openidConnect \
        --key gitea \
        --secret "${GITEA_CLIENT_SECRET}" \
        --auto-discover-url "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" \
        --group-claim-name groups \
        --admin-group cluster-admin \
        --restricted-group guest \
        --group-team-map '{"developer":{"narwhal":["Developers"]},"viewer":{"narwhal":["Viewers"]}}' \
        --skip-local-2fa \
        --scopes "openid profile email groups" 2>/dev/null \
      && echo "Gitea OAuth source 'keycloak' created" \
      || echo "WARN: Gitea OAuth config failed"
  fi
else
  echo "WARN: Gitea pod not found, skipping OAuth2 configuration"
fi

echo "=== Gitea Installation Done ==="

echo ""
echo "=========================================="
echo "Gitea Ready!"
echo "=========================================="
echo ""
echo "Access:"
echo "  kubectl port-forward svc/gitea-http -n devtools 3000:3000"
echo "  URL: http://localhost:3000"
echo "  User: gitea-admin / (see Secret gitea-admin -n devtools)"
echo ""
kubectl get pods -n devtools
