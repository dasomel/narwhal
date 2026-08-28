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
backgroundController:
  replicas: 1
cleanupController:
  replicas: 1
reportsController:
  replicas: 1
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
config:
  oidc:
    # D-authmig: Keycloak OIDC (migrated from Authentik)
    clientID: headlamp
    issuerURL: https://keycloak.${DOMAIN}/realms/narwhal
    scopes: openid,profile,email,groups
    # clientSecret loaded from headlamp-oidc-secret (created by 11-3-keycloak-clients.sh)
    secret:
      create: false
      name: headlamp-oidc-secret
    externalSecret:
      enabled: true
      name: headlamp-oidc-secret
# OIDC requests use the authenticated user's RBAC. Keep this aligned with the
# GitOps Application instead of accepting the chart's cluster-admin default.
clusterRoleBinding:
  clusterRoleName: view
initContainers:
  - name: ca-bundle
    image: ghcr.io/headlamp-k8s/headlamp:v0.42.0
    command: ['sh', '-c', 'cat /etc/ssl/certs/ca-certificates.crt /narwhal-ca/ca.crt > /combined/ca-certificates.crt']
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
    --version 0.42.0 \
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

echo "=== Applying Pod Security Admission labels (KISA-POD-01) ==="
# audit/warn 모드만 사용 — enforce는 기존 워크로드 차단 위험이 있어 의도적으로 제외.
# 이후 생성되는 네임스페이스는 여기 없으므로, 새 ns 추가 시 이 목록도 갱신할 것.
for ns in iam devtools monitoring database dev default cilium-secrets; do
  kubectl get ns "${ns}" >/dev/null 2>&1 && kubectl label ns "${ns}" \
    pod-security.kubernetes.io/audit=baseline \
    pod-security.kubernetes.io/warn=baseline --overwrite || true
done
# 호스트 리소스 접근이 필요한 네임스페이스는 privileged로 명시 (라벨 부재보다 명시가 낫다)
for ns in kube-system istio-system platform-system storage security-system nfs-quota-agent; do
  kubectl get ns "${ns}" >/dev/null 2>&1 && kubectl label ns "${ns}" \
    pod-security.kubernetes.io/audit=privileged \
    pod-security.kubernetes.io/warn=privileged --overwrite || true
done
echo "PSA labels applied"

echo "=== Security Apps Installation Complete ==="
