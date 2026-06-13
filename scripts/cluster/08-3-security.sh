#!/bin/bash
set -euo pipefail

echo "=== Installing Security Apps (Kyverno, Headlamp) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Kyverno (Policy Management)
#=========================================
echo "=== Installing Kyverno ==="
helm repo add kyverno https://kyverno.github.io/kyverno/
helm repo update kyverno

cat > /tmp/kyverno-values.yaml << 'EOF'
admissionController:
  replicas: 1
  webhookTimeout: 5
  # chart 3.7.0 only applies the timeout via the container CLI flag, not the top-level key
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

helm upgrade --install kyverno kyverno/kyverno \
  --namespace platform-system \
  --create-namespace \
  --version 3.7.0 \
  -f /tmp/kyverno-values.yaml || echo "WARN: Kyverno install issue, continuing..."

rm /tmp/kyverno-values.yaml

echo "Kyverno installed"

#=========================================
# Headlamp (Kubernetes UI)
#=========================================
echo "=== Installing Headlamp ==="
helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/
helm repo update headlamp

cat > /tmp/headlamp-values.yaml << 'EOF'
config:
  oidc:
    clientID: apisix
    issuerURL: https://authentik.local.narwhal.io/application/o/apisix/
    scopes: openid,profile,email,groups
    # clientSecret loaded from headlamp-oidc-secret (created by 11-2-authentik-config.sh)
    secret:
      create: false
      name: headlamp-oidc-secret
    externalSecret:
      enabled: true
      name: headlamp-oidc-secret
initContainers:
  - name: ca-bundle
    image: ghcr.io/headlamp-k8s/headlamp:v0.40.0
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

helm upgrade --install headlamp headlamp/headlamp \
  --namespace devtools \
  --create-namespace \
  --version 0.40.0 \
  -f /tmp/headlamp-values.yaml || echo "WARN: Headlamp install issue, continuing..."

rm /tmp/headlamp-values.yaml

# Opt Headlamp out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment headlamp -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

echo "Headlamp installed"

echo "=== Security Apps Installation Complete ==="
