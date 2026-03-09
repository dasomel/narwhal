#!/bin/bash
set -euo pipefail

# 11b-keycloak-realm.sh
# Phase: kcadm 로그인, Realm 생성/설정, Roles 생성, Groups 생성, Users 생성
# Depends on: 11a-keycloak-operator.sh (Keycloak pod must be Ready)

#=========================================
# User passwords (override via env vars for production)
# Defaults are intentionally simple for local dev convenience.
# Override with env vars for production deployments.
#=========================================
ADMIN_USER_PASSWORD="${ADMIN_USER_PASSWORD:-admin}"
DEV_USER_PASSWORD="${DEV_USER_PASSWORD:-dev}"
VIEW_USER_PASSWORD="${VIEW_USER_PASSWORD:-view}"
GUEST_USER_PASSWORD="${GUEST_USER_PASSWORD:-guest}"

export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=== Configuring Keycloak Realm, Roles, Groups, Users ==="

# Get Keycloak pod
KEYCLOAK_POD=$(kubectl get pod -n iam -l app=keycloak -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$KEYCLOAK_POD" ]; then
  echo "ERROR: Keycloak pod not found. Ensure 11a-keycloak-operator.sh completed successfully."
  exit 1
fi

echo "Using Keycloak pod: ${KEYCLOAK_POD}"

# Get auto-generated admin credentials from secret
ADMIN_USER=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.username}' | base64 -d)
ADMIN_PASS=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' | base64 -d)

echo "Admin credentials: ${ADMIN_USER}"

#=========================================
# kcadm login
#=========================================
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 \
  --realm master \
  --user "${ADMIN_USER}" \
  --password "${ADMIN_PASS}" || true

#=========================================
# Create kubernetes realm
#=========================================
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create realms \
  -s realm=kubernetes \
  -s enabled=true \
  -s sslRequired=none || true

#=========================================
# Create realm roles
#=========================================
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
  -s name=cluster-admin -s description="Kubernetes Cluster Admin" || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
  -s name=developer -s description="Kubernetes Developer" || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
  -s name=viewer -s description="Kubernetes Viewer" || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
  -s name=guest -s description="Guest (web UI only)" || true

#=========================================
# Create groups
#=========================================
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
  -s name=cluster-admin || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
  -s name=developer || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
  -s name=viewer || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
  -s name=guest || true

#=========================================
# Create users
#=========================================
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
  -s username=admin -s email=admin@local -s enabled=true -s emailVerified=true \
  -s firstName=Cluster -s lastName=Admin || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
  --username admin --new-password "${ADMIN_USER_PASSWORD}" || true

kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
  -s username=dev -s email=dev@local -s enabled=true -s emailVerified=true \
  -s firstName=Dev -s lastName=User || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
  --username dev --new-password "${DEV_USER_PASSWORD}" || true

kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
  -s username=view -s email=view@local -s enabled=true -s emailVerified=true \
  -s firstName=View -s lastName=User || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
  --username view --new-password "${VIEW_USER_PASSWORD}" || true

kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
  -s username=guest -s email=guest@local -s enabled=true -s emailVerified=true \
  -s firstName=Guest -s lastName=User || true
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
  --username guest --new-password "${GUEST_USER_PASSWORD}" || true

#=========================================
# Assign users to groups
#=========================================
echo "Assigning users to groups..."

# Get user IDs
ADMIN_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.username=="admin") | .id')
DEV_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.username=="dev") | .id')
VIEW_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.username=="view") | .id')
GUEST_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.username=="guest") | .id')

# Get group IDs
CLUSTER_ADMIN_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.name=="cluster-admin") | .id')
DEVELOPER_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.name=="developer") | .id')
VIEWER_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.name=="viewer") | .id')
GUEST_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.name=="guest") | .id')

# Add admin to cluster-admin group
if [ -n "$ADMIN_ID" ] && [ -n "$CLUSTER_ADMIN_GID" ]; then
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${ADMIN_ID}/groups/${CLUSTER_ADMIN_GID}" \
    -r kubernetes -s realm=kubernetes -s userId="${ADMIN_ID}" -s groupId="${CLUSTER_ADMIN_GID}" -n || true
  echo "  -> admin added to cluster-admin"
fi

# Add dev to developer group
if [ -n "$DEV_ID" ] && [ -n "$DEVELOPER_GID" ]; then
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${DEV_ID}/groups/${DEVELOPER_GID}" \
    -r kubernetes -s realm=kubernetes -s userId="${DEV_ID}" -s groupId="${DEVELOPER_GID}" -n || true
  echo "  -> dev added to developer"
fi

# Add view to viewer group
if [ -n "$VIEW_ID" ] && [ -n "$VIEWER_GID" ]; then
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${VIEW_ID}/groups/${VIEWER_GID}" \
    -r kubernetes -s realm=kubernetes -s userId="${VIEW_ID}" -s groupId="${VIEWER_GID}" -n || true
  echo "  -> view added to viewer"
fi

# Add guest to guest group
if [ -n "$GUEST_ID" ] && [ -n "$GUEST_GID" ]; then
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${GUEST_ID}/groups/${GUEST_GID}" \
    -r kubernetes -s realm=kubernetes -s userId="${GUEST_ID}" -s groupId="${GUEST_GID}" -n || true
  echo "  -> guest added to guest"
fi

echo "=== [11b-keycloak-realm.sh] 완료 ==="
