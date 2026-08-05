#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle, never a public repository.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh

echo "=== Installing Networking Apps (MetalLB, APISIX, cert-manager) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# MetalLB (LoadBalancer for bare-metal)
# Cloud: skipped. MetalLB is L2/ARP-based and does not work on Kakao Cloud.
# Ingress is fronted by the Kakao worker LB -> node NodePort 31080/31443 (APISIX).
#=========================================
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: skipping MetalLB (Kakao worker LB fronts APISIX via NodePort)"
else
echo "=== Installing MetalLB ==="

METALLB_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install metallb "$(chart metallb)" \
    --force-conflicts \
    --namespace platform-system \
    --create-namespace \
    --version 0.16.1 \
    --set speaker.tolerations[0].key=node-role.kubernetes.io/control-plane \
    --set speaker.tolerations[0].operator=Exists \
    --set speaker.tolerations[0].effect=NoSchedule; then
    METALLB_OK=true; break
  fi
  echo "MetalLB install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${METALLB_OK}" != true ]; then
  echo "ERROR: MetalLB install failed after 5 attempts." >&2
  exit 1
fi

# Wait for MetalLB controller to be ready
echo "Waiting for MetalLB controller..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=controller -n platform-system --timeout=120s || true

# Apply MetalLB configuration (IP pool and L2 advertisement) with retry
echo "Applying MetalLB configuration..."
METALLB_CFG_OK=false
for attempt in 1 2 3 4 5; do
  if kubectl apply -f /home/vagrant/configs/gitops/resources/metallb-config.yaml 2>&1; then
    echo "MetalLB configuration applied"
    METALLB_CFG_OK=true; break
  fi
  echo "MetalLB config apply attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
# Exhausting this silently leaves the cluster with MetalLB running and no IPAddressPool,
# so every LoadBalancer Service stays <pending> — including the APISIX gateway that
# everything else is reached through. Observed 2026-08-04: the first attempt fails while
# the validating webhook is still coming up, which is why the retries exist; what was
# missing is the failure when they run out.
if [ "${METALLB_CFG_OK}" != true ]; then
  echo "ERROR: MetalLB config (IPAddressPool/L2Advertisement) not applied after 5 attempts." >&2
  exit 1
fi

echo "MetalLB installed"
fi  # end MetalLB (Vagrant only)

#=========================================
# APISIX (API Gateway — replaces Traefik + OAuth2-Proxy)
#=========================================
echo "=== Installing APISIX ==="


# Install APISIX CRDs with server-side apply to avoid field manager conflicts
echo "Applying APISIX CRDs (from apisix-ingress-controller chart)..."
# From the bundle, not `helm pull apisix/...`: a repo-qualified pull needs `helm repo add`
# to have run, and it never does on an airgapped node — the first offline run died here
# with "repo apisix not found". The bundle has carried this exact chart all along, and
# unpacking it locally also pins the version the way --version used to.
rm -rf /tmp/aic-chart && mkdir -p /tmp/aic-chart
tar xzf "$(chart apisix-ingress-controller)" -C /tmp/aic-chart
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
  # D2: chart v2.13.0 defaults apisix.ssl.enabled=false; without this the rendered
  # config.yaml has no ssl block → 443/TLS never listens → Harbor push EOF, OIDC unreachable.
  ssl:
    enabled: true
    containerPort: 9443
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

APISIX_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install apisix "$(chart apisix)" \
    --force-conflicts \
    --namespace platform-system \
    --create-namespace \
    --version 2.13.0 \
    --skip-crds \
    -f /tmp/apisix-values.yaml; then
    APISIX_OK=true; break
  fi
  echo "APISIX install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${APISIX_OK}" != true ]; then
  echo "ERROR: APISIX install failed after 5 attempts." >&2
  exit 1
fi

rm /tmp/apisix-values.yaml

# Patch gateway service type (chart v2.13.0 ignores gateway.type value).
# Vagrant: LoadBalancer via MetalLB. Cloud: NodePort fronted by the Kakao worker LB.
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "Patching APISIX gateway service to NodePort (Kakao worker LB fronts it)..."
  kubectl patch svc apisix-gateway -n platform-system \
    -p '{"spec":{"type":"NodePort"}}' || true
else
  echo "Patching APISIX gateway service to LoadBalancer..."
  kubectl patch svc apisix-gateway -n platform-system \
    -p '{"spec":{"type":"LoadBalancer"},"metadata":{"annotations":{"metallb.universe.tf/loadBalancerIPs":"192.168.56.200"}}}' || true
fi
# D2: chart v2.13.0 also fails to add the 443→9443 port even when gateway.tls.enabled=true;
# add it idempotently via JSON merge patch (duplicate ports are rejected by the API server,
# so the || true is only for the rare case the svc doesn't exist yet on a partial re-run).
echo "Patching APISIX gateway service to add 443/TLS port (D2: chart v2.13.0 bug)..."
kubectl get svc apisix-gateway -n platform-system -o jsonpath='{.spec.ports[*].port}' 2>/dev/null \
  | grep -qw 443 || \
  kubectl patch svc apisix-gateway -n platform-system --type=json \
    -p '[{"op":"add","path":"/spec/ports/-","value":{"name":"apisix-gateway-tls","port":443,"protocol":"TCP","targetPort":9443}}]' || true

# Cloud: pin the exact nodePorts the Kakao worker LB target groups forward to (80->31080, 443->31443).
# Matched by port number (index-independent). Verify live that the chart didn't reassign them.
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "Pinning APISIX gateway nodePorts to 31080/31443 for the Kakao LB..."
  for attempt in 1 2 3 4 5; do
    PORTS_JSON=$(kubectl get svc apisix-gateway -n platform-system -o json 2>/dev/null | python3 -c '
import json,sys
svc=json.load(sys.stdin)
want={80:31080,443:31443}
ports=svc["spec"]["ports"]
for p in ports:
    if p.get("port") in want: p["nodePort"]=want[p["port"]]
print(json.dumps([{"op":"replace","path":"/spec/ports","value":ports}]))
' 2>/dev/null) || true
    if [ -n "${PORTS_JSON}" ] && kubectl patch svc apisix-gateway -n platform-system --type=json -p "${PORTS_JSON}" 2>/dev/null; then
      echo "APISIX nodePorts pinned (80->31080, 443->31443)"
      break
    fi
    echo "  nodePort pin attempt ${attempt}/5 failed, retrying..."; sleep 5
  done
fi

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

APISIX_INGRESS_CONTROLLER_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install apisix-ingress-controller "$(chart apisix-ingress-controller)" \
    --force-conflicts \
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
    --set resources.limits.memory=256Mi \
    --set securityContext.allowPrivilegeEscalation=false \
    --set 'securityContext.capabilities.drop[0]=ALL' \
    --set securityContext.seccompProfile.type=RuntimeDefault; then
    APISIX_INGRESS_CONTROLLER_OK=true; break
  fi
  echo "APISIX ingress controller install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${APISIX_INGRESS_CONTROLLER_OK}" != true ]; then
  echo "ERROR: APISIX ingress controller install failed after 5 attempts." >&2
  exit 1
fi

echo "Waiting for APISIX ingress controller..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=apisix-ingress-controller \
  -n platform-system --timeout=120s || true

echo "APISIX ingress controller installed"

#=========================================
# cert-manager
#=========================================
echo "=== Installing cert-manager ==="

CERT_MANAGER_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install cert-manager "$(chart cert-manager)" \
    --force-conflicts \
    --namespace platform-system \
    --create-namespace \
    --version v1.20.2 \
    --set crds.enabled=true; then
    CERT_MANAGER_OK=true; break
  fi
  echo "cert-manager install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${CERT_MANAGER_OK}" != true ]; then
  echo "ERROR: cert-manager install failed after 5 attempts." >&2
  exit 1
fi

# Wait for cert-manager CRDs and webhook to be usable before applying any cert-manager resources.
# Without this gate the inline ClusterIssuer/Certificate below fail with
# "no matches for kind Certificate in version cert-manager.io/v1".
echo "Waiting for cert-manager CRDs to be established..."
kubectl wait --for=condition=Established crd/certificates.cert-manager.io --timeout=120s
kubectl wait --for=condition=Established crd/clusterissuers.cert-manager.io --timeout=120s
echo "Waiting for cert-manager deployments to roll out..."
kubectl -n platform-system rollout status deploy/cert-manager --timeout=180s
kubectl -n platform-system rollout status deploy/cert-manager-cainjector --timeout=180s
kubectl -n platform-system rollout status deploy/cert-manager-webhook --timeout=180s

# D5: The kube-apiserver runs as a hostNetwork static pod (kubepods cgroup) on master nodes.
# Cilium's socketLB.hostNamespaceOnly=true excludes hostNetwork pods from socket-level BPF LB,
# and the "Host: Legacy" routing mode means no iptables DNAT rules are generated either.
# Result: kube-apiserver cannot reach any Service ClusterIP, so ALL cert-manager webhook calls
# from the API server fail with "no route to host" immediately (EHOSTUNREACH from the Cilium
# BPF cgroup/connect4 hook).  Direct nc/curl from the host DOES work because user.slice
# processes ARE covered by the socket-level BPF hook — only kubepods+hostNetwork is excluded.
#
# Fix: temporarily delete the VWC/MWC so the controller can reconcile status without the
# webhook blocking every status PATCH.  Apply all cert-manager resources in this window,
# poll for Ready, then restore the webhooks.  This is idempotent on re-runs (backup overwrites
# to the same YAML, restore is a kubectl apply).
echo "D5: Saving and temporarily removing cert-manager webhooks to unblock kube-apiserver→webhook path..."
CM_VWC_YAML=$(kubectl get validatingwebhookconfiguration cert-manager-webhook -o yaml 2>/dev/null || true)
CM_MWC_YAML=$(kubectl get mutatingwebhookconfiguration cert-manager-webhook -o yaml 2>/dev/null || true)
kubectl delete validatingwebhookconfiguration cert-manager-webhook --ignore-not-found
kubectl delete mutatingwebhookconfiguration cert-manager-webhook --ignore-not-found

# D5: Guarantee webhook restoration even if a Ready-poll below hits `exit 1`. Without this
# trap, a mid-block failure would leave cert-manager admission disabled cluster-wide until
# the next 08-1 run. The trap fires on any EXIT (success or failure); kubectl apply is
# idempotent so it composes with the explicit happy-path restore at the end of the block.
_restore_cm_webhooks() {
  [ -n "${CM_VWC_YAML:-}" ] && printf '%s' "${CM_VWC_YAML}" | kubectl apply -f - 2>/dev/null || true
  [ -n "${CM_MWC_YAML:-}" ] && printf '%s' "${CM_MWC_YAML}" | kubectl apply -f - 2>/dev/null || true
}
trap _restore_cm_webhooks EXIT

# Create self-signed ClusterIssuer (bootstrap only — do not use directly for app certs)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-cluster-issuer
spec:
  selfSigned: {}
EOF

echo "Waiting for selfsigned-cluster-issuer to be Ready..."
for _i in $(seq 1 24); do
  _STATUS=$(kubectl get clusterissuer selfsigned-cluster-issuer \
    -o jsonpath="{.status.conditions[0].status}" 2>/dev/null || true)
  if [ "${_STATUS}" = "True" ]; then
    echo "selfsigned-cluster-issuer Ready"
    break
  fi
  if [ "${_i}" -eq 24 ]; then
    echo "ERROR: selfsigned-cluster-issuer not Ready after 2 minutes" >&2
    exit 1
  fi
  echo "  attempt ${_i}/24: status=${_STATUS}, waiting 5s..."
  sleep 5
done

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

echo "Waiting for Root CA certificate..."
for _i in $(seq 1 24); do
  _STATUS=$(kubectl get certificate narwhal-root-ca -n platform-system \
    -o jsonpath="{.status.conditions[0].status}" 2>/dev/null || true)
  if [ "${_STATUS}" = "True" ]; then
    echo "narwhal-root-ca Ready"
    break
  fi
  if [ "${_i}" -eq 24 ]; then
    echo "ERROR: narwhal-root-ca not Ready after 2 minutes" >&2
    exit 1
  fi
  echo "  attempt ${_i}/24: status=${_STATUS}, waiting 5s..."
  sleep 5
done

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

echo "Waiting for narwhal-ca-issuer to be Ready..."
for _i in $(seq 1 12); do
  _STATUS=$(kubectl get clusterissuer narwhal-ca-issuer \
    -o jsonpath="{.status.conditions[0].status}" 2>/dev/null || true)
  if [ "${_STATUS}" = "True" ]; then
    echo "narwhal-ca-issuer Ready"
    break
  fi
  if [ "${_i}" -eq 12 ]; then
    echo "ERROR: narwhal-ca-issuer not Ready after 1 minute" >&2
    exit 1
  fi
  echo "  attempt ${_i}/12: status=${_STATUS}, waiting 5s..."
  sleep 5
done

# Re-apply apisix-infra now that cert-manager CRDs + the CA issuer exist. The earlier
# etcd-bootstrap apply (line ~60, before cert-manager was installed) silently skipped the
# bundled narwhal-wildcard-tls Certificate ("no matches for kind Certificate" + || true),
# which later starved 08-6's CA distribution and left gitea/headlamp stuck on the missing
# narwhal-ca-cert secret. This second apply creates the Certificate (etcd stays unchanged).
# Apply while webhooks are still disabled so the Certificate CREATE goes through.
echo "Re-applying apisix-infra to create cert-manager Certificates (webhooks still disabled)..."
helm template narwhal-platform /home/vagrant/configs/gitops/charts/narwhal-platform --set baseDomain="${DOMAIN}" --show-only templates/apisix-infra.yaml 2>/dev/null | kubectl apply -f - || true

echo "Waiting for narwhal-wildcard-tls Certificate..."
for _i in $(seq 1 36); do
  _STATUS=$(kubectl get certificate narwhal-wildcard-tls -n platform-system \
    -o jsonpath="{.status.conditions[0].status}" 2>/dev/null || true)
  if [ "${_STATUS}" = "True" ]; then
    echo "narwhal-wildcard-tls Ready"
    break
  fi
  echo "  attempt ${_i}/36: status=${_STATUS}, waiting 5s..."
  sleep 5
done

# D5: Restore cert-manager webhook configurations now that all resources are created/Ready.
# The webhooks protect ongoing cert lifecycle; restoring them ensures future Certificate
# renewals and GitOps-managed certs are properly validated.
echo "D5: Restoring cert-manager webhook configurations..."
if [ -n "${CM_VWC_YAML}" ]; then
  echo "${CM_VWC_YAML}" | kubectl apply -f - 2>/dev/null || true
fi
if [ -n "${CM_MWC_YAML}" ]; then
  echo "${CM_MWC_YAML}" | kubectl apply -f - 2>/dev/null || true
fi
echo "cert-manager webhook configurations restored"

echo "cert-manager installed with CA issuer"

echo "=== Networking Apps Installation Complete ==="
