#!/bin/bash
set -euo pipefail

# Manifests come from the airgap bundle.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh

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
kubectl apply -n devtools -f "$(manifest argocd-install.yaml)" \
  --server-side --force-conflicts

# Fix ClusterRoleBindings: ArgoCD is installed in devtools, not argocd namespace
# The upstream manifests always use 'argocd' as Subject namespace - must be patched
echo "Fixing ClusterRoleBinding Subject namespaces (argocd -> devtools)..."
for crb in argocd-application-controller argocd-applicationset-controller argocd-server; do
  kubectl patch clusterrolebinding "${crb}" --type='json' \
    -p='[{"op": "replace", "path": "/subjects/0/namespace", "value": "devtools"}]' 2>/dev/null || true
done

# argocd-redis -> Valkey. LICENSE, not performance.
#
# Upstream install.yaml pins public.ecr.aws/docker/library/redis:8.2.3-alpine, and
# Redis 8 is tri-licensed RSALv2 / SSPLv1 / AGPLv3. RSALv2 and SSPLv1 are
# source-available, NOT OSI open source, and RSALv2 forbids offering the software
# as a managed service to third parties. That is the only non-OSI runtime component
# narwhal would otherwise ship, and the airgap bundle REDISTRIBUTES it (see NOTICE).
# Valkey is the BSD-3-Clause fork of Redis 7.2 and is already in the bundle — the
# portal's cache runs the same image, so this adds no pull.
#
# Why the swap is safe, checked rather than assumed:
#   - The container sets `args` but no `command`, so the image ENTRYPOINT runs.
#     Valkey's docker-entrypoint.sh prepends `valkey-server` when the first arg
#     starts with `-` (same rule as the Redis image), so the upstream args
#     `--save '' --appendonly no --requirepass $(REDIS_PASSWORD)` are unchanged
#     valkey-server flags and keep working verbatim.
#   - The pod runs runAsUser 999; the Valkey alpine image's `valkey` user is also
#     uid 999, matching the `redis` user it replaces.
#   - readOnlyRootFilesystem stays satisfiable: `--save ''` plus `--appendonly no`
#     means no disk writes.
#   - THE ONE REAL INCOMPATIBILITY is the CLI. The Valkey image ships valkey-cli
#     and NO redis-cli — its only redis reference is a comment. The PING probe
#     added further down therefore resolves the binary at runtime instead of
#     hardcoding either name.
echo "Repointing argocd-redis at Valkey (BSD-3-Clause) instead of Redis 8..."
kubectl set image deployment/argocd-redis redis=docker.io/valkey/valkey:8-alpine -n devtools


# Patch ArgoCD NetworkPolicies for Istio ambient mesh (HBONE port 15008)
echo "Patching ArgoCD NetworkPolicies for Istio ambient mesh (HBONE port 15008)..."
# NetworkPolicy 생성 대기 (최대 30초)
for i in $(seq 1 30); do
  # No `| grep -q`: grep -q closes the pipe on its first match and pipefail turns kubectl's
  # SIGPIPE into a false "not found" (measured 2/40 on a comparable list). A false negative
  # here only wastes the 30s wait, but the no-pipe form costs nothing.
  if [ -n "$(kubectl get networkpolicy -n devtools -o name 2>/dev/null | grep argocd || true)" ]; then
    break
  fi
  sleep 1
done
for np in $(kubectl get networkpolicy -n devtools -o name 2>/dev/null | grep argocd); do
  kubectl patch "$np" -n devtools --type='json' \
    -p='[{"op": "add", "path": "/spec/ingress/0/ports/-", "value": {"port": 15008, "protocol": "TCP"}}]' 2>/dev/null || true
done

# Give every ArgoCD workload resource requests/limits.
# Upstream install.yaml ships NO resources block, so all 7 components land in the
# BestEffort QoS class -> highest oom_score_adj -> the kernel picks them first when
# a node hits memory pressure. Symptom: application-controller OOMKilled (exit 137)
# seconds after start, CrashLoopBackOff, GitOps reconciliation silently frozen while
# every Application still reports its last-known Synced/Healthy status.
# Requests also keep the scheduler from stacking these onto an already-saturated node,
# which is how the controller ended up on a worker at 93% memory requests.
echo "Setting resource requests/limits on ArgoCD workloads..."
patch_resources() {
  local kind="$1" name="$2" cpu_req="$3" mem_req="$4" cpu_lim="$5" mem_lim="$6"
  kubectl patch "$kind" "$name" -n devtools --type='json' \
    -p="[{\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/resources\", \"value\": {\"requests\": {\"cpu\": \"${cpu_req}\", \"memory\": \"${mem_req}\"}, \"limits\": {\"cpu\": \"${cpu_lim}\", \"memory\": \"${mem_lim}\"}}}]"
}

# application-controller carries the reconcile loop for every Application - by far the
# largest consumer, and the one that was actually being OOM-killed.
patch_resources statefulset argocd-application-controller 250m 512Mi 1000m 2Gi
patch_resources deployment  argocd-repo-server            100m 256Mi 1000m 1Gi
patch_resources deployment  argocd-server                  50m 128Mi  500m 512Mi
patch_resources deployment  argocd-applicationset-controller 50m 128Mi 500m 512Mi
# redis: an OOM kill here reproduces the recurring "redis EOF wedge" (repo-server
# loses its cache, apps go Unknown), so it gets a floor too.
patch_resources deployment  argocd-redis                   50m  64Mi  200m 256Mi
patch_resources deployment  argocd-notifications-controller 25m 64Mi  200m 256Mi
patch_resources deployment  argocd-dex-server              25m  64Mi  100m 128Mi

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

# redis: opt out of ambient. All its CLIENTS (server/repo-server/controller) are
# opted out above, so with redis mesh-enrolled their plaintext connections hit
# ztunnel expecting mTLS under STRICT → dropped with EOF. Symptom: repo-server
# "failed to list refs: EOF" / "retrieve git references from cache: EOF", apps
# stuck Unknown, gitops pin bumps never sync (recurring "redis EOF wedge").
kubectl patch deployment argocd-redis -n devtools --type='json' \
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
  # 13-2-narwhal-portal-bindings.sh issues the portal's API token against this local
  # account, and its header comment has always claimed this script creates it — it did
  # not. The only declaration lived in the GitOps chart
  # (gitops/charts/narwhal-platform/templates/argocd-config.yaml), which ArgoCD does not
  # apply until 14-gitops-bootstrap.sh, one step LATER than the script that needs it. So
  # on every clean install the token endpoint answered
  # "account 'narwhal-portal' does not exist" and 13-2 fell back to a placeholder. It only
  # ever worked when someone re-ran 13-2 by hand after GitOps had caught up, which is why
  # it looked like a timing problem and got "fixed" twice by waiting longer.
  # Value must stay identical to the chart's, so the later GitOps sync is a no-op.
  accounts.narwhal-portal: apiKey,login
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

#=========================================
# Trust the cluster's private CA for repository access.
#
# Needed because every Application's chart now comes from the in-cluster Gitea Helm
# registry instead of a public chart repo. Gitea publishes index.yaml with ABSOLUTE
# download URLs derived from ROOT_URL, so even when ArgoCD is pointed at the in-cluster
# service it is sent out to https://gitea.${DOMAIN}/api/packages/... — served by APISIX
# with a narwhal-CA certificate that ArgoCD does not otherwise trust. Symptom without
# this: every migrated app goes Unknown with
#   error fetching chart: ... tls: failed to verify certificate: x509: signed by unknown authority
# while the chart is sitting in the registry.
#
# argocd-tls-certs-cm is keyed by hostname and read by repo-server; the ArgoCD install
# manifest already mounts it, so populating the key is the whole change. `|| true` because
# a cluster whose wildcard cert has not been issued yet must not fail the install here —
# 08-6 re-runs and this step is idempotent.
#=========================================
echo "=== Trusting the narwhal CA for ArgoCD repository access ==="
ARGOCD_CA=$(kubectl get secret narwhal-wildcard-tls -n platform-system \
  -o jsonpath='{.data.ca\.crt}' 2>/dev/null | base64 -d 2>/dev/null || true)
if [ -n "${ARGOCD_CA}" ]; then
  # Both hostnames. The Application names the in-cluster service, but Gitea's index.yaml
  # redirects the actual download to the external host, and the cert is looked up by the
  # host ArgoCD was configured with — one key alone leaves the other leg unverifiable.
  kubectl create configmap argocd-tls-certs-cm -n devtools \
    --from-literal="gitea.${DOMAIN}=${ARGOCD_CA}" \
    --from-literal="gitea-http.devtools.svc.cluster.local=${ARGOCD_CA}" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl label configmap argocd-tls-certs-cm -n devtools \
    app.kubernetes.io/name=argocd-tls-certs-cm app.kubernetes.io/part-of=argocd --overwrite >/dev/null

  # Declaring the repository is not optional, and this is the part that is easy to miss:
  # populating argocd-tls-certs-cm alone changes nothing, because repo-server only passes
  # `--ca-file` to the `helm pull` subprocess for repositories it knows about. Without this
  # Secret the pull runs against the container's system trust store and every app stays
  # Unknown with `x509: certificate signed by unknown authority` — with the CA sitting in
  # the ConfigMap, unused.
  kubectl apply -f - <<REPOEOF
apiVersion: v1
kind: Secret
metadata:
  name: narwhal-charts-repo
  namespace: devtools
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  name: narwhal-charts
  type: helm
  url: http://gitea-http.devtools.svc.cluster.local:3000/api/packages/gitea-admin/helm
  enableOCI: "false"
REPOEOF
  echo "  narwhal CA + narwhal-charts helm repo registered with ArgoCD"
else
  echo "WARN: narwhal-wildcard-tls has no ca.crt yet — ArgoCD cannot verify" >&2
  echo "      gitea.${DOMAIN}, so Helm chart fetches will fail until 08-6 has run." >&2
fi

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
# CLI 이름을 박아두지 않는다: 이미지가 Valkey로 바뀌면서 redis-cli가 사라졌다
# (Valkey 이미지에는 valkey-cli만 있다). 둘 중 있는 것을 런타임에 고르므로 이미지를
# 되돌려도 probe가 그대로 동작한다 — 하드코딩했다면 probe 실패로 파드가 영영 Ready가
# 되지 않고, 원인은 '레디스가 죽었다'로 보였을 것이다.
# shellcheck disable=SC2016  # $REDIS_PASSWORD/$(...)는 컨테이너 내부 sh에서 확장 (의도적 single quote)
kubectl patch deployment argocd-redis -n devtools --type strategic -p '{
  "spec": {"template": {"spec": {"containers": [{
    "name": "redis",
    "livenessProbe": {
      "exec": {"command": ["sh", "-c", "CLI=$(command -v valkey-cli || command -v redis-cli); response=$($CLI -a \"$REDIS_PASSWORD\" --no-auth-warning ping) && [ \"$response\" = \"PONG\" ]"]},
      "initialDelaySeconds": 10, "periodSeconds": 15, "timeoutSeconds": 5, "failureThreshold": 3
    },
    "readinessProbe": {
      "exec": {"command": ["sh", "-c", "CLI=$(command -v valkey-cli || command -v redis-cli); response=$($CLI -a \"$REDIS_PASSWORD\" --no-auth-warning ping) && [ \"$response\" = \"PONG\" ]"]},
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
# Informational listing — must not decide the script's exit status.
kubectl get pods -n devtools || true
