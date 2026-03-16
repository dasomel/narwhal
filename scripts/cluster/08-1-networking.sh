#!/bin/bash
set -euo pipefail

echo "=== Installing Networking Apps (MetalLB, APISIX, cert-manager) ==="

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
# APISIX (API Gateway — replaces Traefik + OAuth2-Proxy)
#=========================================
echo "=== Installing APISIX ==="

helm repo add apisix https://charts.apiseven.com
helm repo update apisix

# Install APISIX CRDs with server-side apply to avoid field manager conflicts
echo "Applying APISIX CRDs..."
helm pull apisix/apisix --version 2.9.0 --untar --untardir /tmp/apisix-chart
for f in /tmp/apisix-chart/apisix/crds/*.yaml; do
  kubectl apply --server-side --force-conflicts -f "${f}" 2>&1 | tail -1
done
rm -rf /tmp/apisix-chart

# Deploy etcd (uses registry.k8s.io/etcd — no Bitnami)
# etcd is also managed by apisix-infra GitOps resource; apply directly for bootstrap
echo "Deploying etcd for APISIX..."
kubectl apply -f /home/vagrant/configs/gitops/resources/apisix-infra.yaml || true

# Wait for etcd to be ready
echo "Waiting for etcd..."
kubectl wait --for=condition=Available deployment/apisix-etcd -n platform-system --timeout=120s || true

# Install APISIX + Ingress Controller (etcd.enabled=false → uses external etcd above)
cat > /tmp/apisix-values.yaml << 'EOF'
apisix:
  enabled: true
  image:
    repository: apache/apisix
    tag: "3.11.0-debian"
  podLabels:
    istio.io/dataplane-mode: "none"
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
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

helm upgrade --install apisix apisix/apisix \
  --namespace platform-system \
  --create-namespace \
  --version 2.9.0 \
  --skip-crds \
  -f /tmp/apisix-values.yaml || echo "WARN: APISIX install issue, continuing..."

rm /tmp/apisix-values.yaml

# Wait for APISIX gateway to be ready
echo "Waiting for APISIX gateway..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=apisix -n platform-system --timeout=180s || true

echo "APISIX installed"

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
