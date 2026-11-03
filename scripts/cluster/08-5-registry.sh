#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle, never a public repository.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh
source /home/vagrant/scripts/common/lib.sh

echo "=== Installing Registry Apps (Harbor) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Harbor (Container Registry) - ARM64 images
#=========================================
echo "=== Installing Harbor ==="

# Wait for unified PostgreSQL cluster (narwhal-db) to be ready
echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true

# Create namespace first to apply ExternalName service
kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -

# Harbor admin credentials — build harbor-secrets (used by GitOps YAML)
# Key: HARBOR_ADMIN_PASSWORD (required by harbor chart existingSecretAdminPasswordKey)
HARBOR_ADMIN_PASS=$(generate_password)
if ! kubectl get secret harbor-secrets -n devtools &>/dev/null; then
  kubectl create secret generic harbor-secrets \
    --from-literal=HARBOR_ADMIN_PASSWORD="${HARBOR_ADMIN_PASS}" \
    -n devtools
  echo "Harbor admin secret created (harbor-secrets)"
else
  HARBOR_ADMIN_PASS=$(kubectl get secret harbor-secrets -n devtools \
    -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
  echo "Harbor admin secret already exists, reusing"
fi

# Harbor internal shared secrets (secretKey/core/jobservice) — externalized (no plaintext in git)
# Keys read by harbor chart: secretKey (existingSecretSecretKey), secret (core.existingSecret), JOBSERVICE_SECRET (jobservice.existingSecret)
if ! kubectl get secret harbor-shared-secrets -n devtools &>/dev/null; then
  kubectl create secret generic harbor-shared-secrets \
    --from-literal=secretKey="$(openssl rand -hex 8)" \
    --from-literal=secret="$(openssl rand -hex 8)" \
    --from-literal=JOBSERVICE_SECRET="$(openssl rand -hex 8)" \
    -n devtools
  echo "Harbor shared secrets created (harbor-shared-secrets)"
else
  echo "Harbor shared secrets already exist, reusing"
fi

# Harbor DB password — provided by 07-cnpg.sh via narwhal-db-credentials Secret
HARBOR_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
  -o jsonpath='{.data.harbor-password}' | base64 -d)

# harbor-db-credentials: cross-namespace copy for harbor chart (devtools ns)
# Harbor chart external DB existingSecret requires key 'password' in same namespace
if ! kubectl get secret harbor-db-credentials -n devtools &>/dev/null; then
  kubectl create secret generic harbor-db-credentials \
    --from-literal=password="${HARBOR_DB_PASS}" \
    -n devtools
  echo "Harbor DB credentials secret created (harbor-db-credentials in devtools)"
else
  echo "Harbor DB credentials secret already exists"
fi

# Apply ExternalName service to connect to narwhal-db from devtools namespace
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: harbor-db-rw
  namespace: devtools
spec:
  type: ExternalName
  externalName: narwhal-db-rw.database.svc.cluster.local
  ports:
    - port: 5432
      targetPort: 5432
EOF

# D-HARBOR-OIDC-TRUST: Harbor core must trust the Narwhal CA so 11-3 can enable
# oidc_verify_cert. 08-6 later overwrites this secret with narwhal+k8s CA; seeding it here
# keeps harbor-core from sitting in ContainerCreating on a missing secret until 08-6 runs.
if ! kubectl get secret narwhal-ca-cert -n devtools >/dev/null 2>&1; then
  HARBOR_CA=$(kubectl get secret narwhal-wildcard-tls -n platform-system -o jsonpath='{.data.ca\.crt}' 2>/dev/null | base64 -d || true)
  if [ -z "${HARBOR_CA}" ]; then
    echo "ERROR: no CA in narwhal-wildcard-tls (platform-system); Harbor cannot trust Keycloak TLS." >&2
    exit 1
  fi
  kubectl create secret generic narwhal-ca-cert --from-literal=ca.crt="${HARBOR_CA}" -n devtools
fi

HARBOR_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install harbor "$(chart harbor)" \
    --force-conflicts \
    --namespace devtools \
    --version 1.19.1 \
    --set expose.type=clusterIP \
    --set expose.tls.enabled=false \
    --set externalURL=http://harbor.local \
    --set existingSecretAdminPassword=harbor-secrets \
    --set existingSecretAdminPasswordKey=HARBOR_ADMIN_PASSWORD \
    --set existingSecretSecretKey=harbor-shared-secrets \
    --set caBundleSecretName=narwhal-ca-cert \
    --set core.existingSecret=harbor-shared-secrets \
    --set jobservice.existingSecret=harbor-shared-secrets \
    --set-string 'jobservice.podAnnotations.backup\.velero\.io/backup-volumes-excludes=job-logs' \
    --set database.type=external \
    --set database.external.host=harbor-db-rw \
    --set database.external.port=5432 \
    --set database.external.username=harbor \
    --set database.external.existingSecret=harbor-db-credentials \
    --set database.external.coreDatabase=harbor \
    --set persistence.enabled=true \
    --set persistence.persistentVolumeClaim.registry.storageClass=nfs-csi \
    --set persistence.persistentVolumeClaim.registry.size=20Gi \
    --set persistence.persistentVolumeClaim.jobservice.storageClass=nfs-csi \
    --set persistence.persistentVolumeClaim.trivy.storageClass=nfs-csi \
    --set redis.type=internal \
    --set trivy.enabled=false \
    --set metrics.enabled=false \
    --set core.image.repository=ghcr.io/dasomel/goharbor/harbor-core \
    --set core.image.tag=v2.15.1 \
    --set jobservice.image.repository=ghcr.io/dasomel/goharbor/harbor-jobservice \
    --set jobservice.image.tag=v2.15.1 \
    --set registry.registry.image.repository=ghcr.io/dasomel/goharbor/registry-photon \
    --set registry.registry.image.tag=v2.15.1 \
    --set registry.controller.image.repository=ghcr.io/dasomel/goharbor/harbor-registryctl \
    --set registry.controller.image.tag=v2.15.1 \
    --set portal.image.repository=ghcr.io/dasomel/goharbor/harbor-portal \
    --set portal.image.tag=v2.15.1 \
    --set nginx.image.repository=ghcr.io/dasomel/goharbor/nginx-photon \
    --set nginx.image.tag=v2.15.1 \
    --set redis.internal.image.repository=ghcr.io/dasomel/goharbor/redis-photon \
    --set redis.internal.image.tag=v2.15.1 \
    --set exporter.image.repository=ghcr.io/dasomel/goharbor/harbor-exporter \
    --set exporter.image.tag=v2.15.0-build.32; then
    HARBOR_OK=true; break
  fi
  echo "Harbor install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${HARBOR_OK}" != true ]; then
  echo "ERROR: Harbor install failed after 5 attempts." >&2
  exit 1
fi

# Opt Harbor SSO-facing pods out of Istio ambient mesh (cookie handling)
for harbor_deploy in harbor-core harbor-nginx harbor-portal; do
  kubectl patch deployment "${harbor_deploy}" -n devtools --type='json' \
    -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true
done

echo "Harbor installed"

#=========================================
# Harbor: Offline Security DB Project
#=========================================
echo "Configuring Harbor offline security DB project..."

# Wait for Harbor to be ready before API calls
echo "Waiting for Harbor core pod..."
kubectl wait --for=condition=Ready pod -l app=harbor,component=core -n devtools --timeout=300s || true

# Wait for Harbor API to respond via internal ClusterIP
# (harbor.local.narwhal.internal DNS is not yet available — dnsmasq runs at step 10)
HARBOR_CORE_IP=$(kubectl get svc harbor-core -n devtools -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
HARBOR_HEALTH="000"
for attempt in $(seq 1 15); do
  if [ -z "${HARBOR_CORE_IP}" ]; then
    HARBOR_CORE_IP=$(kubectl get svc harbor-core -n devtools -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
  fi
  HARBOR_HEALTH=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' "http://${HARBOR_CORE_IP}/api/v2.0/systeminfo" 2>/dev/null || echo "000")
  if [ "${HARBOR_HEALTH}" = "200" ]; then
    echo "Harbor API ready (internal ClusterIP)"
    break
  fi
  echo "Harbor API not ready (HTTP ${HARBOR_HEALTH}), attempt ${attempt}/15..."
  sleep 10
done

if [ "${HARBOR_HEALTH}" = "200" ]; then
  HARBOR_API="http://${HARBOR_CORE_IP}/api/v2.0"
  # trivy-db project (narwhal#48): this only creates the EMPTY project. The operator-run
  # scripts/airgap/lib/push-security-db.sh lands the offline-fetched Trivy DB/Java DB/checks bundle here, and
  # gitops/resources/trivy-operator.yaml points dbRegistry/javaDbRegistry/
  # policiesBundle.registry at harbor.devtools.svc.cluster.local/trivy-db/... instead
  # of ghcr.io — that is the actual fix for #48's "scan Job pulls the DB live from the
  # internet" gap. Public (no auth) so pulling it needs no credential in gitops/ — the
  # trivy-operator chart has no existingSecret option for dbRepository credentials,
  # only plaintext value fields, which docs/common/agent-operational-rules.md forbids putting in git. Public is an
  # acceptable trade here: this project is airgapped from the real internet in the
  # first place, so an unauthenticated pull is only reachable from inside the cluster
  # network. Idempotent: Harbor 400s on an existing project name, which is fine here.
  curl -sk -X POST "${HARBOR_API}/projects" \
    -H "Content-Type: application/json" \
    -u "admin:${HARBOR_ADMIN_PASS}" \
    -d '{"project_name":"trivy-db","metadata":{"public":"true"}}' 2>/dev/null || true
  echo "Harbor: trivy-db project ensured (public, for narwhal#48 offline vulnerability DB)"

  # D-SECDB-EMPTY-WARN: creating the project does NOT load the DB. Nothing in Phase 2 / up.sh
  # pushes it (push-security-db.sh is the operator-run airgap flow, and a Harbor that was
  # just installed is always empty), so until someone runs it trivy-operator's scan Jobs hit
  # `trivy-db/aquasecurity/trivy-db ... NOT_FOUND`, VulnerabilityReports and
  # InfraAssessmentReports stay at 0 and the Portal Security/Compliance pages are empty with
  # no error anywhere. Say so here, loudly, with the exact commands. Warn, not fail: Harbor
  # and every other component are healthy, and fetching needs internet this step may not have.
  SECDB_REPOS=$(curl -s --max-time 10 -u "admin:${HARBOR_ADMIN_PASS}" \
    "${HARBOR_API}/projects?name=trivy-db" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    print(sum(p.get("repo_count", 0) for p in json.load(sys.stdin) if p.get("name") == "trivy-db"))
except Exception:
    print(-1)' 2>/dev/null || echo -1)
  if [ "${SECDB_REPOS:--1}" != "-1" ] && [ "${SECDB_REPOS}" -lt 3 ] 2>/dev/null; then
    echo "WARN: Harbor project trivy-db has ${SECDB_REPOS}/3 repositories (trivy-db, trivy-java-db, trivy-checks)."
    echo "WARN: Security Dashboard and Compliance infra data stay EMPTY until the security DB is loaded."
    echo "WARN: On a machine with internet and the cluster DNS (see scripts/airgap/README.md, vulnerability DB section):"
    echo "WARN:   scripts/airgap/lib/fetch-security-db.sh   --bundle <dir>"
    echo "WARN:   scripts/airgap/lib/promote-security-db.sh --bundle <dir> --approved-by <name>"
    echo "WARN:   scripts/airgap/lib/push-security-db.sh    --bundle <dir> --ca-cert <narwhal ca.crt> --kube-context <ctx>"
  fi
else
  echo "WARN: Harbor API not available, skipping offline security DB project setup"
fi

echo "=== Registry Apps Installation Complete ==="
