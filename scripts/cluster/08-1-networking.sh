#!/bin/bash
set -euo pipefail

echo "=== Installing Networking Apps (MetalLB, APISIX, cert-manager) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# MetalLB (LoadBalancer for bare-metal)
#=========================================
echo "=== Installing MetalLB ==="
for attempt in 1 2 3 4 5; do
  if helm repo add metallb https://metallb.github.io/metallb && helm repo update metallb; then
    break
  fi
  echo "Helm repo metallb attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

for attempt in 1 2 3 4 5; do
  if helm upgrade --install metallb metallb/metallb \
    --namespace platform-system \
    --create-namespace \
    --version 0.16.1 \
    --set speaker.tolerations[0].key=node-role.kubernetes.io/control-plane \
    --set speaker.tolerations[0].operator=Exists \
    --set speaker.tolerations[0].effect=NoSchedule; then
    break
  fi
  echo "MetalLB install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# Wait for MetalLB controller to be ready
echo "Waiting for MetalLB controller..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=controller -n platform-system --timeout=120s || true

# Apply MetalLB configuration (IP pool and L2 advertisement) with retry
echo "Applying MetalLB configuration..."
for attempt in 1 2 3 4 5; do
  if kubectl apply -f /home/vagrant/configs/gitops/resources/metallb-config.yaml 2>&1; then
    echo "MetalLB configuration applied"
    break
  fi
  echo "MetalLB config apply attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

echo "MetalLB installed"

#=========================================
# APISIX (API Gateway — replaces Traefik + OAuth2-Proxy)
#=========================================
echo "=== Installing APISIX ==="

for attempt in 1 2 3 4 5; do
  if helm repo add apisix https://charts.apiseven.com && helm repo update apisix; then
    break
  fi
  echo "Helm repo apisix attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# Install APISIX CRDs with server-side apply to avoid field manager conflicts
echo "Applying APISIX CRDs (from apisix-ingress-controller chart)..."
rm -rf /tmp/aic-chart
helm pull apisix/apisix-ingress-controller --version 0.14.1 --untar --untardir /tmp/aic-chart
for f in /tmp/aic-chart/apisix-ingress-controller/crds/*.yaml; do
  kubectl apply --server-side --force-conflicts -f "${f}" 2>&1 | tail -1
done
rm -rf /tmp/aic-chart

# Deploy etcd (uses registry.k8s.io/etcd — no Bitnami)
# etcd is also managed by apisix-infra GitOps resource; apply directly for bootstrap
echo "Deploying etcd for APISIX..."
helm template narwhal-platform /home/vagrant/configs/gitops/charts/narwhal-platform --set baseDomain="${DOMAIN}" --show-only templates/apisix-infra.yaml 2>/dev/null | kubectl apply -f - || true

# Wait for etcd to be ready
echo "Waiting for etcd..."
kubectl wait --for=condition=Available deployment/apisix-etcd -n platform-system --timeout=120s || true

# Install APISIX + Ingress Controller (etcd.enabled=false → uses external etcd above)
cat > /tmp/apisix-values.yaml << 'EOF'
apisix:
  enabled: true
  image:
    repository: apache/apisix
    tag: "3.15.0-debian"
  podLabels:
    istio.io/dataplane-mode: "none"
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
  # Kubernetes Secret Provider for OIDC credentials
  # Enables: $secret://kubernetes/k8s-1/apisix-oidc-config/<key>
  config:
    apisix:
      secret_providers:
        - name: kubernetes
          uid: k8s-1
          auth_type: serviceaccount
          apiservers:
            - https://kubernetes.default.svc
gateway:
  type: LoadBalancer
  annotations:
    metallb.universe.tf/loadBalancerIPs: "192.168.56.200"
  http:
    enabled: true
    servicePort: 80
    containerPort: 9080
  tls:
    enabled: true
    servicePort: 443
    containerPort: 9443
admin:
  enabled: true
  type: ClusterIP
  port: 9180
etcd:
  enabled: false
  host:
    - "http://apisix-etcd.platform-system.svc.cluster.local:2379"
  prefix: "/apisix"
  timeout: 30
ingressController:
  enabled: true
  image:
    repository: apache/apisix-ingress-controller
    tag: "1.8.0"
  podLabels:
    istio.io/dataplane-mode: "none"
  config:
    apisix:
      serviceNamespace: platform-system
      adminAPIVersion: "v3"
    kubernetes:
      watchNamespaces: []
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi
tolerations:
  - key: "node.kubernetes.io/disk-pressure"
    operator: "Exists"
    effect: "NoSchedule"
EOF

for attempt in 1 2 3 4 5; do
  if helm upgrade --install apisix apisix/apisix \
    --namespace platform-system \
    --create-namespace \
    --version 2.13.0 \
    --skip-crds \
    --force \
    -f /tmp/apisix-values.yaml; then
    break
  fi
  echo "APISIX install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

rm /tmp/apisix-values.yaml

# Patch gateway service to LoadBalancer (chart v2.13.0 ignores gateway.type value)
echo "Patching APISIX gateway service to LoadBalancer..."
kubectl patch svc apisix-gateway -n platform-system \
  -p '{"spec":{"type":"LoadBalancer"},"metadata":{"annotations":{"metallb.universe.tf/loadBalancerIPs":"192.168.56.200"}}}' || true

# Patch APISIX configmap: fix etcd host and remove auth (chart v2.13.0 uses default etcd.host)
echo "Patching APISIX configmap (etcd host + remove auth)..."
APISIX_CFG_TMP=$(mktemp)
kubectl get configmap apisix -n platform-system -o jsonpath='{.data.config\.yaml}' \
  | grep -v '    user: ' \
  | grep -v '    password: ' \
  | sed 's|"http://etcd.host:2379"|"http://apisix-etcd.platform-system.svc.cluster.local:2379"|g' \
  | sed 's|- 127.0.0.1/24|- 127.0.0.0/24\n      - 0.0.0.0/0|g' \
  > "${APISIX_CFG_TMP}"
# Add Kubernetes Secret Provider (for $secret://kubernetes/k8s-1/... in ApisixRoute plugins)
if ! grep -q 'secret_providers' "${APISIX_CFG_TMP}"; then
  cat >> "${APISIX_CFG_TMP}" << 'SECEOF'

# Kubernetes Secret Provider — enables $secret://kubernetes/k8s-1/<secret>/<key> in routes
secret_providers:
  - name: kubernetes
    uid: k8s-1
    auth_type: serviceaccount
    apiservers:
      - https://kubernetes.default.svc
SECEOF
fi

kubectl create configmap apisix -n platform-system \
  --from-file="config.yaml=${APISIX_CFG_TMP}" \
  --dry-run=client -o yaml | kubectl apply -f - || true
rm -f "${APISIX_CFG_TMP}"

# Restart APISIX to pick up configmap changes
kubectl rollout restart deployment/apisix -n platform-system || true

# Wait for APISIX gateway to be ready
echo "Waiting for APISIX gateway..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=apisix -n platform-system --timeout=180s || true

echo "APISIX installed"

#=========================================
# APISIX Ingress Controller
# Note: apisix/apisix chart v2.13.0 does NOT render ingressController deployment
# despite ingressController.enabled=true in values. Install separately.
#=========================================
echo "=== Installing APISIX Ingress Controller ==="

# Get APISIX admin/viewer keys from the live config (chart v2.13.0 bakes them in at install
# time; the configmap stores the literal key after env-substitution by the APISIX process).
# The grep target is the plain key line that appears AFTER "name: admin" / "name: viewer".
APISIX_ADMIN_KEY=$(kubectl get configmap apisix -n platform-system \
  -o jsonpath='{.data.config\.yaml}' 2>/dev/null \
  | grep -A2 'name: "admin"' | grep '^\s*key:' | awk '{print $2}' | head -1 || true)
APISIX_ADMIN_KEY="${APISIX_ADMIN_KEY:-edd1c9f034335f136f87ad84b625c8f1}"

APISIX_VIEWER_KEY=$(kubectl get configmap apisix -n platform-system \
  -o jsonpath='{.data.config\.yaml}' 2>/dev/null \
  | grep -A2 'name: "viewer"' | grep '^\s*key:' | awk '{print $2}' | head -1 || true)
APISIX_VIEWER_KEY="${APISIX_VIEWER_KEY:-4054f7cf07e344346cd3f287985e76a2}"

# Persist both keys into the Secret that show-credentials.sh and the APISIX deployment
# env vars (secretKeyRef name: apisix-admin-key) expect.  Idempotent via dry-run|apply.
echo "Creating apisix-admin-key secret..."
kubectl create secret generic apisix-admin-key \
  --namespace platform-system \
  --from-literal=key="${APISIX_ADMIN_KEY}" \
  --from-literal=viewer="${APISIX_VIEWER_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

for attempt in 1 2 3 4 5; do
  if helm upgrade --install apisix-ingress-controller apisix/apisix-ingress-controller \
    --namespace platform-system \
    --version 0.14.1 \
    --skip-crds \
    --set image.repository=apache/apisix-ingress-controller \
    --set image.tag="1.8.0" \
    --set "podLabels.istio\\.io/dataplane-mode=none" \
    --set config.apisix.serviceNamespace=platform-system \
    --set config.apisix.serviceName=apisix-admin \
    --set config.apisix.adminKey="${APISIX_ADMIN_KEY}" \
    --set config.apisix.adminAPIVersion=v3 \
    --set resources.requests.cpu=50m \
    --set resources.requests.memory=128Mi \
    --set resources.limits.cpu=200m \
    --set resources.limits.memory=256Mi; then
    break
  fi
  echo "APISIX ingress controller install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

echo "Waiting for APISIX ingress controller..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=apisix-ingress-controller \
  -n platform-system --timeout=120s || true

echo "APISIX ingress controller installed"

#=========================================
# cert-manager
#=========================================
echo "=== Installing cert-manager ==="
for attempt in 1 2 3 4 5; do
  if helm repo add jetstack https://charts.jetstack.io && helm repo update jetstack; then
    break
  fi
  echo "Helm repo jetstack attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

for attempt in 1 2 3 4 5; do
  if helm upgrade --install cert-manager jetstack/cert-manager \
    --namespace platform-system \
    --create-namespace \
    --version v1.20.2 \
    --set crds.enabled=true; then
    break
  fi
  echo "cert-manager install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# Wait for cert-manager CRDs and webhook to be usable before applying any cert-manager resources.
# Without this gate the inline ClusterIssuer/Certificate below fail with
# "no matches for kind Certificate in version cert-manager.io/v1".
echo "Waiting for cert-manager CRDs to be established..."
kubectl wait --for=condition=Established crd/certificates.cert-manager.io --timeout=120s
kubectl wait --for=condition=Established crd/clusterissuers.cert-manager.io --timeout=120s
echo "Waiting for cert-manager webhook to be ready..."
kubectl -n platform-system rollout status deploy/cert-manager-webhook --timeout=180s

# Create self-signed ClusterIssuer (bootstrap only — do not use directly for app certs)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-cluster-issuer
spec:
  selfSigned: {}
EOF

# Create Root CA Certificate (self-signed, valid 10 years)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: narwhal-root-ca
  namespace: platform-system
spec:
  isCA: true
  commonName: "Narwhal IDP Root CA"
  secretName: narwhal-root-ca-secret
  privateKey:
    algorithm: ECDSA
    size: 256
  issuerRef:
    name: selfsigned-cluster-issuer
    kind: ClusterIssuer
  duration: 87600h
  renewBefore: 8760h
EOF

# Wait for CA certificate to be ready
echo "Waiting for Root CA certificate..."
kubectl wait --for=condition=Ready certificate/narwhal-root-ca -n platform-system --timeout=60s

# Create CA ClusterIssuer (signs all application certificates)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: narwhal-ca-issuer
spec:
  ca:
    secretName: narwhal-root-ca-secret
EOF

echo "cert-manager installed with CA issuer"

# Re-apply apisix-infra now that cert-manager CRDs + the CA issuer exist. The earlier
# etcd-bootstrap apply (line ~60, before cert-manager was installed) silently skipped the
# bundled narwhal-wildcard-tls Certificate ("no matches for kind Certificate" + || true),
# which later starved 08-6's CA distribution and left gitea/headlamp stuck on the missing
# narwhal-ca-cert secret. This second apply creates the Certificate (etcd stays unchanged).
echo "Re-applying apisix-infra to create cert-manager Certificates..."
helm template narwhal-platform /home/vagrant/configs/gitops/charts/narwhal-platform --set baseDomain="${DOMAIN}" --show-only templates/apisix-infra.yaml 2>/dev/null | kubectl apply -f - || true

echo "=== Networking Apps Installation Complete ==="
