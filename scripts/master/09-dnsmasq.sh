#!/bin/bash
set -euo pipefail

#=========================================
# dnsmasq Installation for Local DNS
#=========================================
# Resolves *.local.narwhal.io to MetalLB IP (192.168.56.200)
# Listens on master IP (192.168.56.10) port 53
# Forwards all other queries to upstream DNS (8.8.8.8, 8.8.4.4)

MASTER_IP="${MASTER_IP:-192.168.56.10}"
METALLB_IP="${METALLB_IP:-192.168.56.200}"
DOMAIN="${DOMAIN:-local.narwhal.io}"

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

# Resolve all *.local.narwhal.io to MetalLB IP
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

# Start dnsmasq
sudo systemctl start dnsmasq
sudo systemctl enable dnsmasq

# Wait for service to start
sleep 2

# Verify DNS is working
echo ""
echo "=== Testing DNS resolution ==="

# Test local domain
echo "Testing local domain: argocd.${DOMAIN}"
nslookup argocd.${DOMAIN} 127.0.0.1 || echo "Local domain test failed"

# Test external domain (should forward to upstream)
echo ""
echo "Testing external domain: google.com"
nslookup google.com 127.0.0.1 || echo "External domain test failed"

echo ""
echo "=== dnsmasq Installation Done ==="
echo ""
echo "DNS Server: ${MASTER_IP}:53"
echo "Local domain: *.${DOMAIN} -> ${METALLB_IP}"
echo "External domains: forwarded to 8.8.8.8, 8.8.4.4"
echo ""
echo "To use on client machines:"
echo ""
echo "  macOS:"
echo "    sudo mkdir -p /etc/resolver"
echo "    echo 'nameserver ${MASTER_IP}' | sudo tee /etc/resolver/${DOMAIN}"
echo ""
echo "  Linux (systemd-resolved):"
echo "    sudo resolvectl dns eth0 ${MASTER_IP}"
echo "    sudo resolvectl domain eth0 ~${DOMAIN}"
echo ""
sudo systemctl status dnsmasq --no-pager || true
