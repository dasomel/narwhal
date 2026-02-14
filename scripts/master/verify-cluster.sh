#!/bin/bash
set -uo pipefail

#=========================================
# Narwhal IDP Cluster - Comprehensive Verification
#=========================================
# Checks ALL components installed by the provisioning scripts.
# Each check outputs PASS/FAIL with details.
# Exit code: 0 if all pass, 1 if any fail.
#
# Usage:
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/master/verify-cluster.sh"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/master/verify-cluster.sh --quick"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/master/verify-cluster.sh --section nodes"

QUICK_MODE=false
SECTION_FILTER=""
STAGE_FILTER=""
for arg in "$@"; do
  case "${arg}" in
    --quick) QUICK_MODE=true ;;
    --section=*) SECTION_FILTER="${arg#--section=}" ;;
    --stage=*) STAGE_FILTER="${arg#--stage=}" ;;
  esac
done

# Stage definitions:
#   phase1: Cluster infrastructure (nodes, kube-vip, etcd, cilium, core services)
#   phase2-infra: Platform infra (MetalLB, Traefik, cert-manager, TLS, DNS)
#   phase2-apps: Platform apps (DB, monitoring, Keycloak, OIDC, GitOps)
#   full: All checks (default)

# Use local kubeconfig if available (bypasses VIP for reliability)
if [ -f /home/vagrant/.kube/config-local ]; then
  export KUBECONFIG=/home/vagrant/.kube/config-local
fi

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
RESULTS=""

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  RESULTS="${RESULTS}\n  PASS  $1"
  echo "  PASS  $1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  RESULTS="${RESULTS}\n  FAIL  $1"
  echo "  FAIL  $1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  RESULTS="${RESULTS}\n  WARN  $1"
  echo "  WARN  $1"
}

# Stage → section mapping
PHASE1_SECTIONS="nodes kube-vip etcd cilium core"
PHASE2_INFRA_SECTIONS="nodes network tls dns"
PHASE2_APPS_SECTIONS="nodes database keycloak monitoring platform gitops routes"

# Check if a section should run
should_run() {
  local section="$1"
  # Direct section filter
  if [ -n "${SECTION_FILTER}" ]; then
    [ "${SECTION_FILTER}" = "${section}" ] && return 0 || return 1
  fi
  # Stage filter
  if [ -n "${STAGE_FILTER}" ]; then
    case "${STAGE_FILTER}" in
      phase1) echo "${PHASE1_SECTIONS}" | grep -qw "${section}" && return 0 || return 1 ;;
      phase2-infra) echo "${PHASE2_INFRA_SECTIONS}" | grep -qw "${section}" && return 0 || return 1 ;;
      phase2-apps) echo "${PHASE2_APPS_SECTIONS}" | grep -qw "${section}" && return 0 || return 1 ;;
      full|*) return 0 ;;
    esac
  fi
  return 0
}

# Check pod status in a namespace by label
check_pods() {
  local ns="$1" label="$2" expected="$3" name="$4"
  local running
  running=$(kubectl get pods -n "${ns}" -l "${label}" --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${running}" -ge "${expected}" ]; then
    pass "${name}: ${running} Running"
  else
    local actual
    actual=$(kubectl get pods -n "${ns}" -l "${label}" --no-headers 2>/dev/null || echo "NONE")
    fail "${name}: expected >=${expected} Running, got ${running} — ${actual}"
  fi
}

# Check deployment/statefulset ready
check_ready() {
  local kind="$1" ns="$2" name="$3" display="$4"
  local ready
  ready=$(kubectl get "${kind}" -n "${ns}" "${name}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
  local desired
  desired=$(kubectl get "${kind}" -n "${ns}" "${name}" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
  if [ "${ready:-0}" -gt 0 ] && [ "${ready}" = "${desired}" ]; then
    pass "${display}: ${ready}/${desired} Ready"
  else
    fail "${display}: ${ready:-0}/${desired:-0} Ready"
  fi
}

echo "============================================"
echo "Narwhal IDP Cluster - Verification Report"
echo "============================================"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

#=========================================
# 1. NODES
#=========================================
if should_run "nodes"; then
  echo "--- [1/14] Nodes ---"
  NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
  READY_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || echo "0")
  NOTREADY=$(kubectl get nodes --no-headers 2>/dev/null | grep -v " Ready" || echo "")

  if [ "${NODE_COUNT}" -ge 4 ]; then
    pass "Node count: ${NODE_COUNT}"
  else
    fail "Node count: ${NODE_COUNT} (expected >= 4)"
  fi

  if [ "${READY_COUNT}" = "${NODE_COUNT}" ]; then
    pass "All nodes Ready: ${READY_COUNT}/${NODE_COUNT}"
  else
    fail "Nodes NotReady: ${NOTREADY}"
  fi

  # Check master nodes have control-plane role
  MASTER_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | grep -c "control-plane" || echo "0")
  if [ "${MASTER_COUNT}" -ge 2 ]; then
    pass "Control plane nodes: ${MASTER_COUNT}"
  else
    fail "Control plane nodes: ${MASTER_COUNT} (expected >= 2)"
  fi
  echo ""
fi

#=========================================
# 2. KUBE-VIP & VIP
#=========================================
if should_run "kube-vip"; then
  echo "--- [2/14] kube-vip & VIP ---"
  KVIP_PODS=$(kubectl get pods -n kube-system -l k8s-app=kube-vip --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${KVIP_PODS}" -ge 2 ]; then
    pass "kube-vip pods: ${KVIP_PODS} Running"
  elif [ "${KVIP_PODS}" -ge 1 ]; then
    warn "kube-vip pods: ${KVIP_PODS} Running (expected 2 for HA)"
  else
    fail "kube-vip pods: ${KVIP_PODS} Running"
  fi

  if ping -c 1 -W 2 192.168.56.100 &>/dev/null; then
    pass "VIP 192.168.56.100: reachable"
  else
    fail "VIP 192.168.56.100: unreachable"
  fi
  echo ""
fi

#=========================================
# 3. ETCD
#=========================================
if should_run "etcd"; then
  echo "--- [3/14] etcd ---"
  # etcdctl is inside the etcd pod (not installed on host in kubeadm clusters)
  ETCD_POD=$(kubectl get pods -n kube-system -l component=etcd -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  if [ -n "${ETCD_POD}" ]; then
    ETCD_MEMBERS=$(kubectl exec -n kube-system "${ETCD_POD}" -- \
      etcdctl member list \
      --endpoints=https://127.0.0.1:2379 \
      --cacert=/etc/kubernetes/pki/etcd/ca.crt \
      --cert=/etc/kubernetes/pki/etcd/server.crt \
      --key=/etc/kubernetes/pki/etcd/server.key 2>/dev/null | wc -l | tr -d ' ')
  else
    ETCD_MEMBERS=0
  fi
  if [ "${ETCD_MEMBERS}" -ge 2 ]; then
    pass "etcd members: ${ETCD_MEMBERS}"
  else
    fail "etcd members: ${ETCD_MEMBERS} (expected >= 2)"
  fi
  echo ""
fi

#=========================================
# 4. CNI (Cilium)
#=========================================
if should_run "cilium"; then
  echo "--- [4/14] Cilium CNI ---"
  check_pods "kube-system" "k8s-app=cilium" 4 "cilium agents"
  CILIUM_OP_NAME=$(kubectl get deployment -n kube-system -l app.kubernetes.io/name=cilium-operator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null \
    || kubectl get deployment -n kube-system -l name=cilium-operator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null \
    || echo "cilium-operator")
  check_ready "deployment" "kube-system" "${CILIUM_OP_NAME}" "cilium-operator"

  if ! ${QUICK_MODE}; then
    HUBBLE_RELAY=$(kubectl get pods -n kube-system -l k8s-app=hubble-relay --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    if [ "${HUBBLE_RELAY}" -ge 1 ]; then
      pass "hubble-relay: Running"
    else
      warn "hubble-relay: not running (optional)"
    fi
  fi
  echo ""
fi

#=========================================
# 5. CORE SERVICES (CoreDNS, metrics-server, NFS)
#=========================================
if should_run "core"; then
  echo "--- [5/14] Core Services ---"
  check_ready "deployment" "kube-system" "coredns" "CoreDNS"

  # CoreDNS forward rule for local.narwhal.io
  COREDNS_FWD=$(kubectl get configmap coredns -n kube-system -o yaml 2>/dev/null | grep -c "local.narwhal.io" || echo "0")
  if [ "${COREDNS_FWD}" -gt 0 ]; then
    pass "CoreDNS forward rule: local.narwhal.io configured"
  else
    fail "CoreDNS forward rule: local.narwhal.io MISSING"
  fi

  check_ready "deployment" "kube-system" "metrics-server" "metrics-server"
  check_ready "deployment" "kube-system" "csi-nfs-controller" "csi-nfs-controller"

  # CSI NFS node agents
  CSI_NFS_NODES=$(kubectl get pods -n kube-system -l app=csi-nfs-node --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${CSI_NFS_NODES}" -ge 1 ]; then
    pass "csi-nfs-node: ${CSI_NFS_NODES} Running"
  else
    warn "csi-nfs-node: not running"
  fi

  # NFS quota agent (runs in nfs-quota-agent namespace)
  NFS_AGENT=$(kubectl get pods -n nfs-quota-agent --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${NFS_AGENT}" -ge 1 ]; then
    pass "nfs-quota-agent: Running"
  else
    warn "nfs-quota-agent: not running"
  fi
  echo ""
fi

#=========================================
# 6. DATABASE (CNPG)
#=========================================
if should_run "database"; then
  echo "--- [6/14] Database (CNPG) ---"
  CNPG_OP=$(kubectl get pods -n cnpg-system --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${CNPG_OP}" -ge 1 ]; then
    pass "CNPG operator: Running"
  else
    fail "CNPG operator: not running"
  fi

  DB_STATUS=$(kubectl get cluster narwhal-db -n database -o jsonpath='{.status.phase}' 2>/dev/null | tr -d '\n' || echo "UNKNOWN")
  DB_READY=$(kubectl get cluster narwhal-db -n database -o jsonpath='{.status.readyInstances}' 2>/dev/null | tr -d '\n' || echo "0")
  DB_INSTANCES=$(kubectl get cluster narwhal-db -n database -o jsonpath='{.spec.instances}' 2>/dev/null | tr -d '\n' || echo "0")
  if [ "${DB_STATUS}" = "Cluster in healthy state" ] || [ "${DB_READY}" = "${DB_INSTANCES}" ]; then
    pass "narwhal-db: ${DB_READY}/${DB_INSTANCES} instances (${DB_STATUS})"
  else
    fail "narwhal-db: ${DB_READY}/${DB_INSTANCES} instances (${DB_STATUS})"
  fi

  # Pooler
  POOLER=$(kubectl get pods -n database -l cnpg.io/poolerName=narwhal-db-pooler-rw --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${POOLER}" -ge 1 ]; then
    pass "narwhal-db-pooler-rw: Running"
  else
    warn "narwhal-db-pooler-rw: not running"
  fi
  echo ""
fi

#=========================================
# 7. METALLB & TRAEFIK
#=========================================
if should_run "network"; then
  echo "--- [7/14] MetalLB & Traefik ---"
  check_ready "deployment" "metallb-system" "metallb-controller" "MetalLB controller"

  METALLB_SPEAKERS=$(kubectl get pods -n metallb-system -l app.kubernetes.io/component=speaker --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${METALLB_SPEAKERS}" -ge 1 ]; then
    pass "MetalLB speakers: ${METALLB_SPEAKERS} Running"
  else
    fail "MetalLB speakers: not running"
  fi

  check_ready "deployment" "traefik" "traefik" "Traefik"

  # Traefik external IP
  TRAEFIK_IP=$(kubectl get svc traefik -n traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
  if [ -n "${TRAEFIK_IP}" ]; then
    pass "Traefik LoadBalancer IP: ${TRAEFIK_IP}"
  else
    fail "Traefik LoadBalancer IP: not assigned"
  fi

  # Traefik Gateway
  GW_STATUS=$(kubectl get gateway traefik-gateway -n traefik -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}' 2>/dev/null || echo "")
  if [ "${GW_STATUS}" = "True" ]; then
    pass "Traefik Gateway: Accepted"
  else
    warn "Traefik Gateway: ${GW_STATUS:-not found}"
  fi
  echo ""
fi

#=========================================
# 8. CERT-MANAGER & TLS
#=========================================
if should_run "tls"; then
  echo "--- [8/14] cert-manager & TLS ---"
  check_ready "deployment" "cert-manager" "cert-manager" "cert-manager"
  check_ready "deployment" "cert-manager" "cert-manager-webhook" "cert-manager-webhook"
  check_ready "deployment" "cert-manager" "cert-manager-cainjector" "cert-manager-cainjector"

  # ClusterIssuer
  ISSUER_READY=$(kubectl get clusterissuer selfsigned-cluster-issuer -o jsonpath='{.status.conditions[0].status}' 2>/dev/null || echo "")
  if [ "${ISSUER_READY}" = "True" ]; then
    pass "ClusterIssuer selfsigned: Ready"
  else
    fail "ClusterIssuer selfsigned: ${ISSUER_READY:-not found}"
  fi

  # TLS Certificate for *.local.narwhal.io
  CERT_READY=$(kubectl get certificate traefik-tls -n traefik -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
  if [ "${CERT_READY}" = "True" ]; then
    pass "TLS cert *.local.narwhal.io: Ready"
  else
    fail "TLS cert *.local.narwhal.io: ${CERT_READY:-not found}"
  fi
  echo ""
fi

#=========================================
# 9. DNS
#=========================================
if should_run "dns"; then
  echo "--- [9/14] DNS ---"
  # dnsmasq service
  if systemctl is-active --quiet dnsmasq 2>/dev/null; then
    pass "dnsmasq: active"
  else
    fail "dnsmasq: not active"
  fi

  # Host DNS resolution
  HOST_DNS=$(nslookup keycloak.local.narwhal.io 127.0.0.1 2>/dev/null | grep -c "192.168.56.200" || echo "0")
  if [ "${HOST_DNS}" -gt 0 ]; then
    pass "Host DNS: keycloak.local.narwhal.io -> 192.168.56.200"
  else
    fail "Host DNS: keycloak.local.narwhal.io resolution failed"
  fi

  # Pod DNS (only if not quick mode)
  if ! ${QUICK_MODE}; then
    POD_DNS=$(kubectl run verify-dns-$$ --rm -i --restart=Never --image=busybox:1.36 -- nslookup keycloak.local.narwhal.io 2>/dev/null | grep -c "192.168.56.200" | tr -d '\n' || echo "0")
    if [ "${POD_DNS}" -gt 0 ]; then
      pass "Pod DNS: keycloak.local.narwhal.io -> 192.168.56.200"
    else
      fail "Pod DNS: keycloak.local.narwhal.io resolution failed (CoreDNS forward rule?)"
    fi
  fi
  echo ""
fi

#=========================================
# 10. KEYCLOAK & OIDC
#=========================================
if should_run "keycloak"; then
  echo "--- [10/14] Keycloak & OIDC ---"
  # Keycloak operator
  check_ready "deployment" "keycloak" "keycloak-operator" "Keycloak operator"

  # Keycloak instance
  KC_PODS=$(kubectl get pods -n keycloak -l app=keycloak --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${KC_PODS}" -ge 1 ]; then
    pass "Keycloak: ${KC_PODS} Running"
  else
    fail "Keycloak: not running"
  fi

  # OIDC HTTPS endpoint
  OIDC_CODE=$(curl -sk -o /dev/null -w '%{http_code}' https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration 2>/dev/null || echo "000")
  if [ "${OIDC_CODE}" = "200" ]; then
    pass "OIDC HTTPS endpoint: HTTP ${OIDC_CODE}"
  else
    fail "OIDC HTTPS endpoint: HTTP ${OIDC_CODE} (expected 200)"
  fi

  # API server OIDC flags
  if sudo grep -q "oidc-issuer-url" /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null; then
    OIDC_URL=$(sudo grep "oidc-issuer-url" /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null | head -1)
    if echo "${OIDC_URL}" | grep -q "https://"; then
      pass "API server OIDC: HTTPS configured"
    else
      fail "API server OIDC: HTTP detected (must be HTTPS for K8s 1.35+)"
    fi
  else
    warn "API server OIDC: not configured (OIDC HTTPS may not have been reachable during install)"
  fi
  echo ""
fi

#=========================================
# 11. MONITORING (Prometheus, Grafana, Loki, Tempo)
#=========================================
if should_run "monitoring"; then
  echo "--- [11/14] Monitoring ---"
  # Prometheus
  PROM_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${PROM_PODS}" -ge 1 ]; then
    pass "Prometheus: Running"
  else
    fail "Prometheus: not running"
  fi

  # Grafana
  check_ready "deployment" "monitoring" "prometheus-grafana" "Grafana"

  # Alertmanager
  ALERT_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=alertmanager --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${ALERT_PODS}" -ge 1 ]; then
    pass "Alertmanager: Running"
  else
    warn "Alertmanager: not running"
  fi

  # Loki
  LOKI_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=loki --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${LOKI_PODS}" -ge 1 ]; then
    pass "Loki: Running"
  else
    fail "Loki: not running"
  fi

  # Promtail
  PROMTAIL_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=promtail --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${PROMTAIL_PODS}" -ge 1 ]; then
    pass "Promtail: ${PROMTAIL_PODS} Running"
  else
    fail "Promtail: not running"
  fi

  # Tempo
  TEMPO_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=tempo --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${TEMPO_PODS}" -ge 1 ]; then
    pass "Tempo: Running"
  else
    fail "Tempo: not running"
  fi
  echo ""
fi

#=========================================
# 12. PLATFORM APPS
#=========================================
if should_run "platform"; then
  echo "--- [12/14] Platform Apps ---"
  # Kyverno
  check_ready "deployment" "kyverno" "kyverno-admission-controller" "Kyverno admission"
  check_ready "deployment" "kyverno" "kyverno-background-controller" "Kyverno background"

  # Headlamp
  check_ready "deployment" "headlamp" "headlamp" "Headlamp"

  # OAuth2-Proxy
  check_ready "deployment" "oauth2-proxy" "oauth2-proxy" "OAuth2-Proxy"

  # SeaweedFS
  SWFS_MASTER=$(kubectl get pods -n seaweedfs -l app.kubernetes.io/component=master --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${SWFS_MASTER}" -ge 1 ]; then
    pass "SeaweedFS master: Running"
  else
    fail "SeaweedFS master: not running"
  fi

  SWFS_FILER=$(kubectl get pods -n seaweedfs -l app.kubernetes.io/component=filer --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${SWFS_FILER}" -ge 1 ]; then
    pass "SeaweedFS filer: Running"
  else
    fail "SeaweedFS filer: not running"
  fi

  # Harbor
  HARBOR_CORE=$(kubectl get pods -n harbor -l component=core --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${HARBOR_CORE}" -ge 1 ]; then
    pass "Harbor core: Running"
  else
    fail "Harbor core: not running"
  fi

  # OpenBao
  OPENBAO=$(kubectl get pods -n openbao -l app.kubernetes.io/name=openbao --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${OPENBAO}" -ge 1 ]; then
    pass "OpenBao: Running"
  else
    warn "OpenBao: not running (requires manual unseal)"
  fi

  # Velero
  check_ready "deployment" "velero" "velero" "Velero"
  echo ""
fi

#=========================================
# 13. GITOPS (Gitea, ArgoCD)
#=========================================
if should_run "gitops"; then
  echo "--- [13/14] GitOps ---"
  # Gitea
  GITEA_PODS=$(kubectl get pods -n gitea -l app.kubernetes.io/name=gitea --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${GITEA_PODS}" -ge 1 ]; then
    pass "Gitea: Running"
  else
    fail "Gitea: not running"
  fi

  # ArgoCD
  check_ready "deployment" "argocd" "argocd-server" "ArgoCD server"
  check_ready "deployment" "argocd" "argocd-repo-server" "ArgoCD repo-server"

  ARGOCD_CTRL=$(kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-application-controller --no-headers 2>/dev/null | grep -c "Running" || echo "0")
  if [ "${ARGOCD_CTRL}" -ge 1 ]; then
    pass "ArgoCD app-controller: Running"
  else
    fail "ArgoCD app-controller: not running"
  fi

  # App-of-Apps
  AOA_STATUS=$(kubectl get application idp-apps -n argocd -o jsonpath='{.status.sync.status}' 2>/dev/null || echo "")
  AOA_HEALTH=$(kubectl get application idp-apps -n argocd -o jsonpath='{.status.health.status}' 2>/dev/null || echo "")
  if [ -n "${AOA_STATUS}" ]; then
    pass "App-of-Apps: sync=${AOA_STATUS}, health=${AOA_HEALTH}"
  else
    warn "App-of-Apps: not found"
  fi
  echo ""
fi

#=========================================
# 14. GATEWAY ROUTES (HTTP/HTTPS access)
#=========================================
if should_run "routes"; then
  echo "--- [14/14] Gateway Routes ---"
  ROUTES=(
    "argocd:argocd"
    "grafana:monitoring"
    "gitea:gitea"
    "harbor:harbor"
    "keycloak:keycloak"
    "headlamp:headlamp"
    "openbao:openbao"
    "oauth2-proxy:oauth2-proxy"
    "hubble:kube-system"
  )

  for entry in "${ROUTES[@]}"; do
    ROUTE_NAME="${entry%%:*}"
    ROUTE_NS="${entry##*:}"
    ROUTE_EXISTS=$(kubectl get httproute "${ROUTE_NAME}" -n "${ROUTE_NS}" -o jsonpath='{.metadata.name}' 2>/dev/null || echo "")
    if [ -n "${ROUTE_EXISTS}" ]; then
      pass "HTTPRoute ${ROUTE_NAME}.local.narwhal.io: exists"
    else
      fail "HTTPRoute ${ROUTE_NAME}: not found in ${ROUTE_NS}"
    fi
  done

  # HTTPS connectivity test (only if not quick mode)
  if ! ${QUICK_MODE}; then
    echo ""
    echo "  --- HTTPS Connectivity ---"
    for host in argocd grafana gitea keycloak; do
      CODE=$(curl -sk -o /dev/null -w '%{http_code}' "https://${host}.local.narwhal.io" 2>/dev/null || echo "000")
      case "${CODE}" in
        200|301|302|303|307|308)
          pass "https://${host}.local.narwhal.io: HTTP ${CODE}" ;;
        *)
          fail "https://${host}.local.narwhal.io: HTTP ${CODE}" ;;
      esac
    done
  fi
  echo ""
fi

#=========================================
# 15. PROBLEM PODS (global check)
#=========================================
echo "--- Problem Pods ---"
PROBLEM_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | grep -Ev "Running|Completed" || echo "")
if [ -z "${PROBLEM_PODS}" ]; then
  pass "No problem pods"
else
  PROBLEM_COUNT=$(echo "${PROBLEM_PODS}" | wc -l | tr -d ' ')
  fail "${PROBLEM_COUNT} problem pods:"
  echo "${PROBLEM_PODS}" | while read -r line; do
    echo "         ${line}"
  done
fi

PENDING_PODS=$(kubectl get pods -A --field-selector=status.phase=Pending --no-headers 2>/dev/null || echo "")
if [ -z "${PENDING_PODS}" ]; then
  pass "No pending pods"
else
  PENDING_COUNT=$(echo "${PENDING_PODS}" | wc -l | tr -d ' ')
  fail "${PENDING_COUNT} pending pods"
fi
echo ""

#=========================================
# SUMMARY
#=========================================
TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT))
echo "============================================"
echo "VERIFICATION SUMMARY"
echo "============================================"
echo "  Total checks: ${TOTAL}"
echo "  PASS: ${PASS_COUNT}"
echo "  FAIL: ${FAIL_COUNT}"
echo "  WARN: ${WARN_COUNT}"
echo ""

TOTAL_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
RUNNING_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | grep -c "Running" || echo "0")
echo "  Cluster: $(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ') nodes, ${TOTAL_PODS} pods (${RUNNING_PODS} running)"
echo ""

if [ "${FAIL_COUNT}" -eq 0 ]; then
  echo "  RESULT: ALL CHECKS PASSED"
  echo "============================================"
  exit 0
else
  echo "  RESULT: ${FAIL_COUNT} CHECKS FAILED"
  echo "============================================"
  exit 1
fi
