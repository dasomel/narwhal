#!/bin/bash
set -euo pipefail

echo "=== Installing Security Apps (Kyverno, Headlamp, OAuth2-Proxy) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Kyverno (Policy Management)
#=========================================
echo "=== Installing Kyverno ==="
helm repo add kyverno https://kyverno.github.io/kyverno/
helm repo update kyverno

helm upgrade --install kyverno kyverno/kyverno \
  --namespace platform-system \
  --create-namespace \
  --version 3.7.0 \
  --set admissionController.replicas=1 \
  --set backgroundController.replicas=1 \
  --set cleanupController.replicas=1 \
  --set reportsController.replicas=1 || echo "WARN: Kyverno install issue, continuing..."

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
    clientID: headlamp
    issuerURL: https://keycloak.local.narwhal.io/realms/kubernetes
    scopes: openid,profile,email,groups
    # clientSecret loaded from headlamp-oidc-secret (created by 11-keycloak.sh)
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

#=========================================
# OAuth2 Proxy (Gateway Authentication)
#=========================================
echo "=== Installing OAuth2 Proxy ==="
helm repo add oauth2-proxy https://oauth2-proxy.github.io/manifests
helm repo update oauth2-proxy

# oauth2-proxy-secrets is created by 11-keycloak.sh (cookie-secret + client-secret)
# Here we only configure non-secret settings; existingSecret handles credentials
cat > /tmp/oauth2-proxy-values.yaml << 'EOF'
replicaCount: 1
config:
  clientID: oauth2-proxy
  # clientSecret and cookieSecret loaded from existingSecret (created by 11-keycloak.sh)
  existingSecret: oauth2-proxy-secrets
  configFile: |-
    provider = "keycloak-oidc"
    provider_display_name = "Keycloak"
    oidc_issuer_url = "https://keycloak.local.narwhal.io/realms/kubernetes"
    redirect_url = "https://oauth2-proxy.local.narwhal.io/oauth2/callback"
    upstreams = ["static://200"]
    email_domains = ["*"]
    cookie_secure = true
    cookie_domains = [".local.narwhal.io"]
    whitelist_domains = [".local.narwhal.io"]
    set_xauthrequest = true
    set_authorization_header = true
    pass_access_token = true
    pass_authorization_header = true
    skip_provider_button = true
    code_challenge_method = "S256"
    insecure_oidc_skip_issuer_verification = true
    ssl_insecure_skip_verify = true
    allowed_groups = ["cluster-admin", "developer", "viewer"]
extraArgs:
  - --skip-jwt-bearer-tokens=true
service:
  type: ClusterIP
  portNumber: 80
EOF

helm upgrade --install oauth2-proxy oauth2-proxy/oauth2-proxy \
  --namespace iam \
  --create-namespace \
  --version 10.1.3 \
  -f /tmp/oauth2-proxy-values.yaml || echo "WARN: OAuth2 Proxy install issue, continuing..."

rm /tmp/oauth2-proxy-values.yaml

# Opt OAuth2-Proxy out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment oauth2-proxy -n iam --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

echo "OAuth2 Proxy installed"

echo "=== Security Apps Installation Complete ==="
