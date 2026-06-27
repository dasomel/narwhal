#!/bin/bash
set -euo pipefail

#=========================================
# dnsmasq Installation for Local DNS
#=========================================
# Resolves *.local.narwhal.internal to MetalLB IP (192.168.56.200)
# Listens on master IP (192.168.56.10) port 53
# Forwards all other queries to upstream DNS (8.8.8.8, 8.8.4.4)

MASTER_IP="${MASTER_IP:-192.168.56.10}"
METALLB_IP="${METALLB_IP:-192.168.56.200}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"
SKIP_COREDNS="${SKIP_COREDNS:-false}"
MASTER_IP_BASE="${MASTER_IP_BASE:-192.168.56.1}"
MASTER_COUNT="${MASTER_COUNT:-3}"

echo "=== Installing dnsmasq for local domain resolution ==="
echo "Master IP: ${MASTER_IP}"
echo "MetalLB IP: ${METALLB_IP}"
echo "Domain: *.${DOMAIN}"

# Install dnsmasq
sudo apt-get update
sudo apt-get install -y dnsmasq

# Stop dnsmasq temporarily
sudo systemctl stop dnsmasq || true

# Disable systemd-resolved stub listener to free port 53
if systemctl is-active --quiet systemd-resolved; then
  echo "Configuring systemd-resolved to use dnsmasq..."
  sudo mkdir -p /etc/systemd/resolved.conf.d

  # Configure systemd-resolved to NOT use stub listener
  # and use dnsmasq (127.0.0.1) as DNS
  sudo tee /etc/systemd/resolved.conf.d/dnsmasq.conf << 'EOF'
[Resolve]
DNS=127.0.0.1
FallbackDNS=8.8.8.8 8.8.4.4
DNSStubListener=no
EOF

  sudo systemctl restart systemd-resolved
fi

# Ensure /etc/resolv.conf uses localhost (dnsmasq)
echo "Configuring /etc/resolv.conf..."
sudo rm -f /etc/resolv.conf
sudo tee /etc/resolv.conf << EOF
# Managed by dnsmasq setup script
nameserver 127.0.0.1
nameserver 8.8.8.8
EOF

# Configure dnsmasq
sudo tee /etc/dnsmasq.d/local.conf << EOF
# Listen on master IP and localhost
listen-address=${MASTER_IP}
listen-address=127.0.0.1
bind-interfaces

# Standard DNS port
port=53

# Resolve all *.local.narwhal.internal to MetalLB IP
address=/${DOMAIN}/${METALLB_IP}

# Upstream DNS servers for all other domains
server=8.8.8.8
server=8.8.4.4

# Do not read /etc/resolv.conf for upstream servers (we define them above)
no-resolv

# Cache settings
cache-size=1000

# Don't forward short names
domain-needed

# Never forward addresses in the non-routed address spaces
bogus-priv
EOF

# Remove default config if exists
sudo rm -f /etc/dnsmasq.d/default 2>/dev/null || true

# Ensure dnsmasq restarts reliably after reboot
sudo mkdir -p /etc/systemd/system/dnsmasq.service.d
cat <<EOF | sudo tee /etc/systemd/system/dnsmasq.service.d/restart.conf
[Unit]
After=network-online.target
Wants=network-online.target

[Service]
Restart=always
RestartSec=5
EOF

# Start dnsmasq
sudo systemctl daemon-reload
sudo systemctl start dnsmasq
sudo systemctl enable dnsmasq

# Wait for service to start
sleep 2

# Verify DNS is working
echo ""
echo "=== Testing DNS resolution ==="

# Test local domain
echo "Testing local domain: argocd.${DOMAIN}"
nslookup "argocd.${DOMAIN}" 127.0.0.1 || echo "Local domain test failed"

# Test external domain (should forward to upstream)
echo ""
echo "Testing external domain: google.com"
nslookup google.com 127.0.0.1 || echo "External domain test failed"

if [ "${SKIP_COREDNS}" = "true" ]; then
  echo "=== Skipping CoreDNS configuration (SKIP_COREDNS=true) ==="
else
  #=========================================
  # CoreDNS Forward Rule for Pod-internal DNS
  #=========================================
  # Pods use CoreDNS (10.96.0.10), not dnsmasq, for DNS resolution.
  # Without this rule, Pods cannot resolve *.local.narwhal.internal.
  # Forward to both master dnsmasq instances for HA.
  # Build dynamic master DNS list for CoreDNS forward
  MASTER_DNS_LIST=""
  for idx in $(seq 0 $((MASTER_COUNT - 1))); do
    MASTER_DNS_LIST="${MASTER_DNS_LIST}${MASTER_IP_BASE}${idx} "
  done
  MASTER_DNS_LIST="${MASTER_DNS_LIST% }"  # trim trailing space
  echo "=== Configuring CoreDNS to forward local.narwhal.internal to dnsmasq (${MASTER_DNS_LIST}) ==="

  COREDNS_CM=$(kubectl get configmap coredns -n kube-system -o json 2>/dev/null || echo "")
  if [ -n "${COREDNS_CM}" ]; then
    # Check if forward rule already exists
    if echo "${COREDNS_CM}" | grep -q "local.narwhal.internal"; then
      echo "CoreDNS forward rule for local.narwhal.internal already exists"
    else
      # Add forward rule using kubectl patch
      COREFILE=$(echo "${COREDNS_CM}" | yq -r '.data.Corefile')
      # Replace /etc/resolv.conf with explicit upstream DNS servers
      # Master nodes have /etc/resolv.conf pointing to 127.0.0.1 (dnsmasq),
      # which creates a forwarding loop inside CoreDNS pods
      COREFILE=$(echo "${COREFILE}" | sed 's|forward \. /etc/resolv.conf|forward . 8.8.8.8 8.8.4.4|g')
      NEW_COREFILE="${DOMAIN}:53 {
    errors
    cache 30
    forward . ${MASTER_DNS_LIST}
}
${COREFILE}"
      kubectl create configmap coredns -n kube-system \
        --from-literal=Corefile="${NEW_COREFILE}" \
        --dry-run=client -o yaml | kubectl apply -f -

      # Restart CoreDNS to pick up the change
      kubectl rollout restart deployment coredns -n kube-system 2>/dev/null || true
      kubectl wait --for=condition=Ready pod -l k8s-app=kube-dns -n kube-system --timeout=60s || true
      echo "CoreDNS forward rule added for ${DOMAIN} -> ${MASTER_DNS_LIST}"
    fi
  else
    echo "WARN: CoreDNS configmap not found, skipping forward rule"
  fi

  #=========================================
  # CoreDNS Hairpin Fix
  #=========================================
  # In-cluster pods cannot reach MetalLB external IPs through Cilium (hairpin routing).
  # Solution: add a hosts zone in CoreDNS that maps *.local.narwhal.internal directly to
  # the APISIX ClusterIP, bypassing the MetalLB external IP entirely.
  echo ""
  echo "=== Configuring CoreDNS hairpin fix (*.${DOMAIN} -> APISIX ClusterIP) ==="

  # Wait for APISIX service to be available
  APISIX_IP=""
  for attempt in $(seq 1 30); do
    APISIX_IP=$(kubectl get svc apisix-gateway -n platform-system -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
    if [ -n "${APISIX_IP}" ]; then
      echo "APISIX ClusterIP: ${APISIX_IP}"
      break
    fi
    echo "Waiting for APISIX service (attempt ${attempt}/30)..."
    sleep 10
  done

  if [ -z "${APISIX_IP}" ]; then
    echo "WARN: APISIX service not found in platform-system, skipping hairpin fix"
  else
    COREDNS_CM_CURRENT=$(kubectl get configmap coredns -n kube-system -o json 2>/dev/null || echo "")
    if [ -z "${COREDNS_CM_CURRENT}" ]; then
      echo "WARN: CoreDNS configmap not found, skipping hairpin fix"
    elif echo "${COREDNS_CM_CURRENT}" | grep -q "hairpin" && echo "${COREDNS_CM_CURRENT}" | grep -q "${APISIX_IP}"; then
      echo "CoreDNS hairpin fix already applied with correct IP (${APISIX_IP})"
    else
      COREFILE_CURRENT=$(echo "${COREDNS_CM_CURRENT}" | yq -r '.data.Corefile')

      # Build master DNS list for hairpin zone fallback
      HAIRPIN_DNS_LIST=""
      for idx in $(seq 0 $((MASTER_COUNT - 1))); do
        HAIRPIN_DNS_LIST="${HAIRPIN_DNS_LIST}${MASTER_IP_BASE}${idx} "
      done
      HAIRPIN_DNS_LIST="${HAIRPIN_DNS_LIST% }"

      # Append hairpin zone block to existing Corefile
      # The hosts plugin maps all known *.local.narwhal.internal hostnames to the
      # APISIX ClusterIP so pods route internally instead of through MetalLB.
      HAIRPIN_ZONE="${DOMAIN}:53 {
    # hairpin: in-cluster pods -> APISIX ClusterIP (bypasses MetalLB)
    errors
    cache 30
    hosts {
        ${APISIX_IP} argocd.${DOMAIN}
        ${APISIX_IP} grafana.${DOMAIN}
        ${APISIX_IP} gitea.${DOMAIN}
        ${APISIX_IP} harbor.${DOMAIN}
        ${APISIX_IP} authentik.${DOMAIN}
        ${APISIX_IP} headlamp.${DOMAIN}
        ${APISIX_IP} openbao.${DOMAIN}
        ${APISIX_IP} apisix-dashboard.${DOMAIN}
        ${APISIX_IP} hubble.${DOMAIN}
        fallthrough
    }
    forward . ${HAIRPIN_DNS_LIST}
}"

      # The forward-only zone for the domain was added above; replace it with the hairpin zone
      # which already includes a forward fallback, to avoid duplicate zone blocks.
      if echo "${COREFILE_CURRENT}" | grep -q "^${DOMAIN}:53"; then
        # Replace the existing simple forward zone with the hairpin zone
        COREFILE_UPDATED=$(echo "${COREFILE_CURRENT}" | awk -v zone="${DOMAIN}:53" -v replacement="${HAIRPIN_ZONE}" '
          BEGIN { skip=0; found=0 }
          $0 ~ "^"zone {
            if (!found) {
              print replacement
              found=1
            }
            skip=1
            next
          }
          skip && /^}/ { skip=0; next }
          skip { next }
          { print }
        ')
      else
        COREFILE_UPDATED="${HAIRPIN_ZONE}
${COREFILE_CURRENT}"
      fi

      kubectl create configmap coredns -n kube-system \
        --from-literal=Corefile="${COREFILE_UPDATED}" \
        --dry-run=client -o yaml | kubectl apply -f -

      kubectl rollout restart deployment coredns -n kube-system 2>/dev/null || true
      kubectl wait --for=condition=Ready pod -l k8s-app=kube-dns -n kube-system --timeout=60s || true
      echo "CoreDNS hairpin fix applied: *.${DOMAIN} -> ${APISIX_IP} (APISIX ClusterIP)"
    fi
  fi

  # Test Pod DNS resolution via CoreDNS (verifies worker nodes can also resolve)
  echo ""
  echo "=== Testing Pod DNS resolution via CoreDNS ==="
  kubectl run dns-pod-test-$$ --rm -i --restart=Never --image=busybox:1.36 \
    -- nslookup "argocd.${DOMAIN}" 2>/dev/null || echo "Pod DNS test warning (non-fatal)"
fi

echo ""
echo "=== dnsmasq Installation Done ==="
echo ""
echo "DNS Server: ${MASTER_IP}:53"
echo "Local domain: *.${DOMAIN} -> ${METALLB_IP}"
echo "External domains: forwarded to 8.8.8.8, 8.8.4.4"
echo ""
echo "To use on client machines:"
echo ""
echo "  macOS (HA - all masters):"
echo "    sudo mkdir -p /etc/resolver"
RESOLVER_CMD="printf '"
for idx in $(seq 0 $((MASTER_COUNT - 1))); do
  RESOLVER_CMD="${RESOLVER_CMD}nameserver ${MASTER_IP_BASE}${idx}\n"
done
RESOLVER_CMD="${RESOLVER_CMD}'"
echo "    ${RESOLVER_CMD} | sudo tee /etc/resolver/${DOMAIN}"
echo ""
echo "  Linux (systemd-resolved):"
echo "    sudo resolvectl dns eth0 ${MASTER_IP}"
echo "    sudo resolvectl domain eth0 ~${DOMAIN}"
echo ""
sudo systemctl status dnsmasq --no-pager || true
