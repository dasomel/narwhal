#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle, never a public repository.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh

echo "=== Installing Security Apps (Kyverno, Headlamp) ==="

DOMAIN="${DOMAIN:-local.narwhal.internal}"
export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Kyverno (Policy Management)
#=========================================
echo "=== Installing Kyverno ==="

cat > /tmp/kyverno-values.yaml << 'EOF'
admissionController:
  replicas: 1
  webhookTimeout: 5
  # chart 3.8.1 only applies the timeout via the container CLI flag, not the top-level key
  container:
    extraArgs:
      webhookTimeout: 5
    resources:
      limits:
        cpu: 500m
backgroundController:
  replicas: 1
  resources:
    limits:
      cpu: 300m
cleanupController:
  replicas: 1
  resources:
    limits:
      cpu: 200m
reportsController:
  replicas: 1
  resources:
    limits:
      cpu: 300m
config:
  webhooks:
    namespaceSelector:
      matchExpressions:
        - key: kubernetes.io/metadata.name
          operator: NotIn
          values:
            - kube-system
            - istio-system
            - platform-system
EOF

KYVERNO_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install kyverno "$(chart kyverno)" \
    --force-conflicts \
    --namespace platform-system \
    --create-namespace \
    --version 3.8.1 \
    -f /tmp/kyverno-values.yaml; then
    KYVERNO_OK=true; break
  fi
  echo "Kyverno install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${KYVERNO_OK}" != true ]; then
  echo "ERROR: Kyverno install failed after 5 attempts." >&2
  exit 1
fi

rm /tmp/kyverno-values.yaml

echo "Kyverno installed"

#=========================================
# Headlamp (Kubernetes UI)
#=========================================
echo "=== Installing Headlamp ==="

cat > /tmp/headlamp-values.yaml << EOF
image:
  registry: ghcr.io
  repository: headlamp-k8s/headlamp
  tag: v0.46.0
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    memory: 512Mi
securityContext:
  # D-PSB-UID: mirrors gitops/charts/narwhal-apps/templates/headlamp.yaml (CIS 5.7.3 KSV020/KSV021 need > 10000)
  runAsUser: 10100
  runAsGroup: 10100
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault
config:
  oidc:
    # D-authmig: Keycloak OIDC (migrated from Authentik)
    clientID: headlamp
    issuerURL: https://keycloak.${DOMAIN}/realms/narwhal
    scopes: openid,profile,email,groups
    autoLogin: true
    # clientSecret loaded from headlamp-oidc-secret (created by 11-3-keycloak-clients.sh)
    secret:
      create: false
      name: headlamp-oidc-secret
    externalSecret:
      enabled: true
      name: headlamp-oidc-secret
      hasScopes: true
# OIDC requests use the authenticated user's RBAC. Keep this aligned with the
# GitOps Application instead of accepting the chart's cluster-admin default.
clusterRoleBinding:
  clusterRoleName: view
initContainers:
  - name: ca-bundle
    image: ghcr.io/headlamp-k8s/headlamp:v0.46.0
    command: ['sh', '-c', 'cat /etc/ssl/certs/ca-certificates.crt /narwhal-ca/ca.crt > /combined/ca-certificates.crt']
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      runAsNonRoot: true
      runAsUser: 10100
      runAsGroup: 10100
      capabilities:
        drop:
          - ALL
      seccompProfile:
        type: RuntimeDefault
    resources:
      requests:
        cpu: 10m
        memory: 16Mi
      limits:
        cpu: 100m
        memory: 64Mi
    volumeMounts:
      - name: narwhal-ca
        mountPath: /narwhal-ca
        readOnly: true
      - name: combined-certs
        mountPath: /combined
volumes:
  - name: narwhal-ca
    secret:
      secretName: narwhal-ca-cert
  - name: combined-certs
    emptyDir: {}
volumeMounts:
  - name: combined-certs
    mountPath: /etc/ssl/certs/ca-certificates.crt
    subPath: ca-certificates.crt
    readOnly: true
EOF

# ClusterRoleBinding.roleRef is immutable. Earlier Phase 2 runs used the chart
# default (cluster-admin), whereas GitOps deliberately requires view. Recreate
# only this chart-owned binding when its roleRef drifts so the Helm upgrade can
# converge; a matching binding is left untouched on normal re-runs.
HEADLAMP_ROLE_REF="$(kubectl get clusterrolebinding headlamp-admin \
  -o jsonpath='{.roleRef.apiGroup}:{.roleRef.kind}:{.roleRef.name}' 2>/dev/null || true)"
if [ -n "${HEADLAMP_ROLE_REF}" ] \
  && [ "${HEADLAMP_ROLE_REF}" != "rbac.authorization.k8s.io:ClusterRole:view" ]; then
  echo "Recreating headlamp-admin ClusterRoleBinding with roleRef ClusterRole/view"
  kubectl delete clusterrolebinding headlamp-admin
fi

HEADLAMP_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install headlamp "$(chart headlamp)" \
    --force-conflicts \
    --namespace devtools \
    --create-namespace \
    --version 0.46.0 \
    -f /tmp/headlamp-values.yaml; then
    HEADLAMP_OK=true; break
  fi
  echo "Headlamp install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${HEADLAMP_OK}" != true ]; then
  echo "ERROR: Headlamp install failed after 5 attempts." >&2
  exit 1
fi

rm /tmp/headlamp-values.yaml

# Opt Headlamp out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment headlamp -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

echo "Headlamp installed"

# D-PSA-LABELS-14 (KISA-POD-01): tolerant early PSA pass (no --require-all).
# security-system (08-5), istio-system (09), iam (11), dev (11-4/ArgoCD), and chaos-testing
# (ArgoCD in step 14) legitimately do not exist yet here, so missing namespaces are skipped
# while non-NotFound API errors still fail loudly. The strict final pass (--require-all) runs
# at the end of 14-gitops-bootstrap.sh once all 14 namespaces exist.
PSA_HELPER="/home/vagrant/scripts/cluster/lib/apply-psa-labels.sh"
if [ ! -f "${PSA_HELPER}" ]; then
  PSA_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/apply-psa-labels.sh"
fi
bash "${PSA_HELPER}"

echo "=== Security Apps Installation Complete ==="
