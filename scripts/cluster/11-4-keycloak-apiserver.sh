#!/bin/bash
set -euo pipefail

# 11-4-keycloak-apiserver.sh
# Phase: K8s API 서버 OIDC 설정, master-2/master-3 SSH 전파, ClusterRoleBindings 생성 (RBAC)
# Depends on: 11-2-keycloak-config.sh (Keycloak OIDC endpoint must be ready)

export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=== Configuring Kubernetes API Server for OIDC and RBAC ==="

#=========================================
# Create Kubernetes RBAC for OIDC groups
#=========================================
echo "=== Creating Kubernetes RBAC for OIDC ==="

# Create dev namespace for developer workloads
kubectl create namespace dev --dry-run=client -o yaml | kubectl apply -f -

# ClusterRoleBinding for cluster-admin group → platform-admin ClusterRole
# NOTE: platform-admin ClusterRole is defined in gitops/resources/rbac-policies.yaml
# and deployed by ArgoCD at step 14. Creating CRB here with platform-admin reference
# ensures no roleRef conflict when ArgoCD syncs rbac-policies.yaml.
# roleRef is immutable — delete existing CRB first to handle any prior cluster-admin binding.
kubectl delete clusterrolebinding oidc-cluster-admin --ignore-not-found
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-cluster-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: platform-admin
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:cluster-admin
EOF

# RoleBinding: developer group gets edit in dev namespace
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-developer-edit
  namespace: dev
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: edit
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developer
EOF

# RoleBinding: developer group gets view in devtools and monitoring
for ns in devtools monitoring; do
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-developer-view
  namespace: ${ns}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developer
EOF
done

# RoleBinding: viewer group gets view in dev, devtools, and monitoring
for ns in dev devtools monitoring; do
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-viewer-view
  namespace: ${ns}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:viewer
EOF
done

# guest group: no K8s RBAC (web UI OIDC only)

#=========================================
# Configure API Server for OIDC
#=========================================
echo "=== Configuring API Server for OIDC ==="

DOMAIN="${DOMAIN:-local.narwhal.internal}"
# K8s 1.35+ requires HTTPS for --oidc-issuer-url; HTTP causes API server crash
OIDC_ISSUER_URL="https://keycloak.${DOMAIN}/realms/narwhal"
APISERVER_MANIFEST="/etc/kubernetes/manifests/kube-apiserver.yaml"

# Verify HTTPS OIDC endpoint is reachable before activating
echo "Verifying OIDC issuer HTTPS endpoint..."
OIDC_REACHABLE=false
for attempt in {1..15}; do
  HTTP_CODE=$(curl -sk -o /dev/null -w '%{http_code}' "${OIDC_ISSUER_URL}/.well-known/openid-configuration" 2>/dev/null) || HTTP_CODE="000"
  if [ "${HTTP_CODE}" = "200" ]; then
    OIDC_REACHABLE=true
    echo "OIDC endpoint reachable (HTTP ${HTTP_CODE})"
    break
  fi
  echo "OIDC endpoint not ready (HTTP ${HTTP_CODE}), attempt ${attempt}/15..."
  sleep 10
done

if [ "${OIDC_REACHABLE}" = "false" ]; then
  echo "WARN: OIDC HTTPS endpoint not reachable. Skipping API server OIDC activation."
  echo "  Possible causes:"
  echo "    - cert-manager TLS certificate not issued yet"
  echo "    - APISIX Gateway not routing keycloak.${DOMAIN}"
  echo "    - DNS not resolving keycloak.${DOMAIN}"
  echo "  Run scripts in order: 08-1-networking.sh..08-6-tls-routes.sh → 10-dnsmasq.sh → 11-keycloak.sh → 11-2-keycloak-config.sh"
else
  # Migrate from Authentik to Keycloak if Authentik OIDC URL is present
  if grep -q "authentik" "${APISERVER_MANIFEST}" 2>/dev/null; then
    echo "Detected Authentik OIDC URL in API server manifest. Migrating to Keycloak..."
    sudo sed -i "s|https://authentik.${DOMAIN}/application/o/kubernetes/|${OIDC_ISSUER_URL}|g" "${APISERVER_MANIFEST}"
    echo "Migrated OIDC issuer from Authentik to Keycloak"

    # Wait for API server to restart after manifest change
    sleep 15
    for i in {1..30}; do
      if kubectl get nodes &>/dev/null; then
        echo "API server is ready with Keycloak OIDC"
        break
      fi
      echo "Waiting for API server... ($i/30)"
      sleep 5
    done
  fi

  # Check if OIDC flags already exist
  if ! grep -q "oidc-issuer-url" "${APISERVER_MANIFEST}" 2>/dev/null; then
    # Extract Keycloak TLS CA certificate for API server OIDC validation
    # Use narwhal-root-ca-secret (the signing CA) rather than parsing TLS handshake,
    # which may return only the leaf cert depending on openssl version.
    echo "Extracting Keycloak TLS CA certificate from narwhal-root-ca-secret..."
    if kubectl get secret -n platform-system narwhal-root-ca-secret &>/dev/null; then
      kubectl get secret -n platform-system narwhal-root-ca-secret \
        -o jsonpath='{.data.tls\.crt}' | base64 -d | sudo tee /etc/kubernetes/pki/oidc-ca.crt > /dev/null
      echo "CA cert extracted from narwhal-root-ca-secret"
    else
      # Fallback: extract last cert in chain (root CA) from TLS handshake
      echo "Fallback: extracting CA from TLS handshake..."
      openssl s_client -connect "keycloak.${DOMAIN}:443" -showcerts </dev/null 2>/dev/null \
        | awk '/-----BEGIN CERTIFICATE-----/{c=""} {c=c $0 "\n"} /-----END CERTIFICATE-----/{last=c} END{printf "%s", last}' \
        | sudo tee /etc/kubernetes/pki/oidc-ca.crt > /dev/null
    fi
    # Verify the extracted CA cert is valid
    openssl x509 -in /etc/kubernetes/pki/oidc-ca.crt -noout -subject 2>/dev/null \
      && echo "CA cert verified OK" || echo "WARN: CA cert may be invalid"

    # Use yq to safely add OIDC flags to the command array
    # NOTE: Do NOT quote the URL value with shell quotes - yq handles YAML escaping
    sudo yq -i ".spec.containers[0].command += [
      \"--oidc-issuer-url=${OIDC_ISSUER_URL}\",
      \"--oidc-client-id=kubernetes\",
      \"--oidc-username-claim=preferred_username\",
      \"--oidc-groups-claim=groups\",
      \"--oidc-username-prefix=oidc:\",
      \"--oidc-groups-prefix=oidc:\",
      \"--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt\"
    ]" "${APISERVER_MANIFEST}"

    echo "OIDC flags added to API server. Waiting for restart..."

    # Wait for API server to restart (static pod manager detects manifest change)
    sleep 15
    for i in {1..30}; do
      if kubectl get nodes &>/dev/null; then
        echo "API server is ready with OIDC"
        break
      fi
      echo "Waiting for API server... ($i/30)"
      sleep 5
    done
  else
    echo "OIDC already configured in API server"
  fi

  # Apply OIDC CA and flags to master-2 and master-3 via SSH
  # Master IPs: master-2=192.168.56.11, master-3=192.168.56.12
  MASTER_IPS="${MASTER_IPS:-192.168.56.11 192.168.56.12}"
  MASTER_COUNT="${MASTER_COUNT:-3}"
  IDX=2
  for MASTER_IP in ${MASTER_IPS}; do
    if [ "${IDX}" -gt "${MASTER_COUNT}" ]; then break; fi
    echo "Applying OIDC CA + flags to master-${IDX} (${MASTER_IP})..."
    # Use sshpass with Vagrant default password (same pattern as 02-join-control-plane.sh:19)
    # Plain ssh/scp fails because masters have no key trust between them in this Vagrant env
    sshpass -p "vagrant" scp -o StrictHostKeyChecking=no /etc/kubernetes/pki/oidc-ca.crt \
      "vagrant@${MASTER_IP}:/tmp/oidc-ca.crt" 2>/dev/null || true
    sshpass -p "vagrant" ssh -o StrictHostKeyChecking=no "vagrant@${MASTER_IP}" "
      sudo cp /tmp/oidc-ca.crt /etc/kubernetes/pki/oidc-ca.crt
      sudo openssl x509 -in /etc/kubernetes/pki/oidc-ca.crt -noout -subject 2>/dev/null \
        && echo 'CA cert OK on master-${IDX}' || echo 'WARN: CA cert invalid on master-${IDX}'
      # Migrate from Authentik to Keycloak if needed
      if sudo grep -q 'authentik' /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null; then
        sudo sed -i 's|https://authentik.${DOMAIN}/application/o/kubernetes/|${OIDC_ISSUER_URL}|g' /etc/kubernetes/manifests/kube-apiserver.yaml
        echo 'Migrated OIDC issuer from Authentik to Keycloak on master-${IDX}'
      elif ! sudo grep -q 'oidc-issuer-url' /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null; then
        sudo yq -i \".spec.containers[0].command += [
          \\\"--oidc-issuer-url=${OIDC_ISSUER_URL}\\\",
          \\\"--oidc-client-id=kubernetes\\\",
          \\\"--oidc-username-claim=preferred_username\\\",
          \\\"--oidc-groups-claim=groups\\\",
          \\\"--oidc-username-prefix=oidc:\\\",
          \\\"--oidc-groups-prefix=oidc:\\\",
          \\\"--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt\\\"
        ]\" /etc/kubernetes/manifests/kube-apiserver.yaml
        echo 'OIDC flags added to master-${IDX}'
      else
        # CA file may have been empty on initial run — restart API server to reload it
        sudo crictl stop \$(sudo crictl ps -q --name kube-apiserver 2>/dev/null) 2>/dev/null || true
        echo 'OIDC already configured on master-${IDX}, restarted to reload CA'
      fi
    " 2>/dev/null || echo "WARN: Could not apply OIDC to master-${IDX} (${MASTER_IP})"
    IDX=$((IDX + 1))
  done
fi

echo "=== [11-4-keycloak-apiserver.sh] 완료 ==="
