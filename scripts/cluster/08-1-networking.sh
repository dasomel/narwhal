#!/bin/bash
set -euo pipefail

echo "=== Installing Networking Apps (MetalLB, Traefik, cert-manager) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# MetalLB (LoadBalancer for bare-metal)
#=========================================
echo "=== Installing MetalLB ==="
helm repo add metallb https://metallb.github.io/metallb
helm repo update metallb

helm upgrade --install metallb metallb/metallb \
  --namespace platform-system \
  --create-namespace \
  --version 0.15.3 \
  --set speaker.tolerations[0].key=node-role.kubernetes.io/control-plane \
  --set speaker.tolerations[0].operator=Exists \
  --set speaker.tolerations[0].effect=NoSchedule || echo "WARN: MetalLB install issue, continuing..."

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
# Traefik (Gateway API Controller)
#=========================================
echo "=== Installing Traefik ==="

# Install Gateway API CRDs (standard + experimental) with server-side apply
# to avoid field manager conflicts when Traefik Helm chart tries to install its own copies
echo "Installing Gateway API experimental CRDs..."
kubectl apply --server-side --force-conflicts -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/experimental-install.yaml 2>&1 | grep -E "created|configured|unchanged|applied" || true

helm repo add traefik https://traefik.github.io/charts
helm repo update traefik

# Extract and apply Traefik CRDs separately with --server-side to avoid conflicts
echo "Applying Traefik CRDs with server-side apply..."
helm pull traefik/traefik --version 39.0.0 --untar --untardir /tmp/traefik-chart
for f in /tmp/traefik-chart/traefik/crds/*.yaml; do
  kubectl apply --server-side --force-conflicts -f "${f}" 2>&1 | tail -1
done
rm -rf /tmp/traefik-chart

helm upgrade --install traefik traefik/traefik \
  --namespace platform-system \
  --create-namespace \
  --version 39.0.0 \
  --skip-crds \
  --set service.type=LoadBalancer \
  --set ports.web.port=8000 \
  --set ports.web.exposedPort=80 \
  --set ports.websecure.port=8443 \
  --set ports.websecure.exposedPort=443 \
  --set ingressRoute.dashboard.enabled=true \
  --set providers.kubernetesGateway.enabled=true \
  --set providers.kubernetesCRD.enabled=true \
  --set providers.kubernetesCRD.allowExternalNameServices=true \
  --set providers.kubernetesIngress.enabled=false \
  --set gateway.enabled=false \
  --set logs.general.level=INFO || echo "WARN: Traefik install issue, continuing..."

echo "Traefik installed"

#=========================================
# cert-manager
#=========================================
echo "=== Installing cert-manager ==="
helm repo add jetstack https://charts.jetstack.io
helm repo update jetstack

helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace platform-system \
  --create-namespace \
  --version v1.19.3 \
  --set crds.enabled=true || echo "WARN: cert-manager install issue, continuing..."

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

echo "=== Networking Apps Installation Complete ==="
