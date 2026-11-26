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
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh --quick"
#   PROVIDER=kakao DOMAIN=kakao.narwhal.internal bash scripts/test/verify-cluster.sh
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh --section nodes"

# shellcheck source=scripts/common/lib-topology.sh
source "$(dirname "$0")/../common/lib-topology.sh"
_OPENBAO_BK_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common/lib-openbao-backup.sh"
if [ -r "${_OPENBAO_BK_LIB}" ]; then
  # shellcheck source=scripts/common/lib-openbao-backup.sh
  source "${_OPENBAO_BK_LIB}"
fi

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
#   phase2-infra: Platform infra (MetalLB, APISIX, cert-manager, TLS, DNS)
#   phase2-apps: Platform apps (DB, monitoring, Keycloak, OIDC, GitOps)
#   full: All checks (default)

# PROVIDER follows show-credentials.sh; cloud callers must set PROVIDER=kakao.
PROVIDER="${PROVIDER:-vagrant}"
[ "${PROVIDER}" = "local" ] && PROVIDER="vagrant"
DOMAIN="${DOMAIN:-local.narwhal.internal}"

# Use local kubeconfig if available (bypasses VIP for reliability)
if [ -f /home/vagrant/.kube/config-local ]; then
  export KUBECONFIG=/home/vagrant/.kube/config-local
fi

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_COUNT=0
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

skip() {
  SKIP_COUNT=$((SKIP_COUNT + 1))
  RESULTS="${RESULTS}\n  SKIP  $1"
  echo "  SKIP  $1"
}

# D-576-PROBE-DENY: count ztunnel inbound rejections of a node-originated kubelet probe.
# Args: <ztunnel log file> <pod IP> <node CiliumInternalIP>. A probe that lost its
# 169.254.7.127 SNAT arrives from the node's cilium_host IP and hits mesh-wide STRICT, so
# src == host IP + dst == pod + istio_converted_static_strict is the discriminator. A genuine
# plaintext client has another src; a healthy probe never reaches ztunnel. Pinned by R307.
ambient_probe_denials() {
  grep -F "src.addr=${3}:" "${1}" | grep -F "dst.addr=${2}:" \
    | grep -F 'direction="inbound"' | grep -F "istio_converted_static_strict" | grep -c . || true
}

_node_priv() {
  ${priv[@]+"${priv[@]}"} "$@"
}

_node_stat() {
  local target="$1"
  if _node_priv stat -L -c '%a %U:%G' "${target}" 2>/dev/null; then
    return 0
  fi
  _node_priv stat -L -f '%Mp%Lp %Su:%Sg' "${target}" 2>/dev/null
}

# D-NODE-HARDENING-LIVE: verifies CIS 1.1.9 (CNI config files <=600, no suid/sgid/sticky, and at
# least one non-hidden *.conf/*.conflist/*.json config present) and CIS 1.1.12 (/var/lib/etcd <=700
# etcd:etcd, no suid/sgid/sticky) directly on the node filesystem alongside trivy-operator's
# nodeCollector mounts (KCV0056 via /etc/cni mount, KCV0059 via /etc/passwd+/etc/group).
# Cost/coverage limit: verify-cluster.sh runs this only on the single local node when
# IS_CLUSTER_NODE=true (SKIPs when run remotely); Trivy CMD-0009/KCV0056 and CMD-0010/KCV0057
# evaluate control-plane (nodeType: master) nodes only and treat empty `values: []` as passing,
# so worker nodes (and missing/dotfile-only CNI dirs) rely on harden-node-files.sh during
# provisioning/join and on this on-node check where it is run. Escape hatch: pass [root_dir] for
# fixture testing.
# Args: [root_dir] [cni_owner] [etcd_owner].
verify_node_file_hardening() {
  local root_dir="${1:-/}"
  local expected_cni_owner="${2:-root:root}"
  local expected_etcd_owner="${3:-etcd:etcd}"
  local prefix="${root_dir%/}"
  [ "${root_dir}" = "/" ] && prefix=""
  local cni_dir="${prefix}/etc/cni/net.d"
  local etcd_dir="${prefix}/var/lib/etcd"
  local etcd_manifest="${prefix}/etc/kubernetes/manifests/etcd.yaml"
  local -a priv=()

  if [ "${root_dir}" = "/" ] && [ "$(id -u)" -ne 0 ]; then
    if sudo -n true 2>/dev/null; then
      priv=(sudo -n)
    else
      warn "CIS 1.1.9/1.1.12 node file hardening: passwordless sudo unavailable"
      return 0
    fi
  fi

  if ! _node_priv test -d "${cni_dir}" || _node_priv test -L "${cni_dir}"; then
    fail "CIS 1.1.9 CNI config dir missing or symlink: ${cni_dir}"
  elif ! _node_priv test -r "${cni_dir}" || ! _node_priv test -x "${cni_dir}"; then
    fail "CIS 1.1.9 CNI config dir unreadable: ${cni_dir}"
  else
    local cni_files cni_count=0 cni_conf_count=0 cni_bad=""
    if ! cni_files=$(_node_priv find -L "${cni_dir}" -mindepth 1 -maxdepth 1 -type f 2>/dev/null); then
      fail "CIS 1.1.9 CNI config dir scan failed (unreadable entry or broken symlink): ${cni_dir}"
    else
      cni_files=$(printf '%s\n' "${cni_files}" | sort)
      while IFS= read -r f; do
        [ -n "${f}" ] || continue
        cni_count=$((cni_count + 1))
        case "${f##*/}" in
          .*) ;;
          *.conf|*.conflist|*.json) cni_conf_count=$((cni_conf_count + 1)) ;;
        esac
        local info mode owner
        info=$(_node_stat "${f}" || echo "")
        mode="${info%% *}"
        owner="${info#* }"
        if [ -z "${info}" ] || ! [[ "${mode}" =~ ^[0-7]+$ ]] || [ $((8#${mode} & 8#7177)) -ne 0 ] || [ "${owner}" != "${expected_cni_owner}" ]; then
          cni_bad="${cni_bad} ${f##*/}=${mode:-?}:${owner:-?}"
        fi
      done <<< "${cni_files}"
      if [ "${cni_conf_count}" -eq 0 ]; then
        fail "CIS 1.1.9 CNI config files: none found in ${cni_dir}"
      elif [ -n "${cni_bad}" ]; then
        fail "CIS 1.1.9 CNI file perms (expected <=600 ${expected_cni_owner}):${cni_bad}"
      else
        pass "CIS 1.1.9 CNI file perms: ${cni_count} file(s) <=600 (${expected_cni_owner})"
      fi
    fi
  fi

  if _node_priv test -d "${etcd_dir}" && ! _node_priv test -L "${etcd_dir}"; then
    local etcd_info etcd_mode etcd_owner
    etcd_info=$(_node_stat "${etcd_dir}" || echo "")
    etcd_mode="${etcd_info%% *}"
    etcd_owner="${etcd_info#* }"
    if [ -n "${etcd_info}" ] && [[ "${etcd_mode}" =~ ^[0-7]+$ ]] && [ $((8#${etcd_mode} & 8#7077)) -eq 0 ] && [ "${etcd_owner}" = "${expected_etcd_owner}" ]; then
      pass "CIS 1.1.12 etcd data dir: ${etcd_dir} (${etcd_mode} ${etcd_owner})"
    else
      fail "CIS 1.1.12 etcd data dir: ${etcd_dir} is ${etcd_mode:-?} ${etcd_owner:-?} (expected <=700 ${expected_etcd_owner})"
    fi
  elif _node_priv test -f "${etcd_manifest}"; then
    fail "CIS 1.1.12 etcd data dir missing or symlink: ${etcd_dir}"
  fi

  # D-NODE-VERIFY-COVERAGE (2026-11-22): directly assert node controls where upstream
  # node-collector returns empty values (CMD-0019/0020/0021 PKI, CMD-0028/0029 kubelet CA,
  # kubelet systemd drop-in under /usr/lib/systemd/system/kubelet.service.d, and kubelet
  # config.yaml CIS 4.1.9 perms). Report missing kubelet runtime settings
  # (CIS 4.2.6/4.2.12/4.2.13) at WARN level with a pointer to docs.
  local pki_dir="${prefix}/etc/kubernetes/pki"
  if ! _node_priv test -d "${pki_dir}" || _node_priv test -L "${pki_dir}"; then
    fail "CIS 1.1.19-21/4.1.7 PKI dir missing or symlink: ${pki_dir}"
  elif ! _node_priv test -r "${pki_dir}" || ! _node_priv test -x "${pki_dir}"; then
    fail "CIS 1.1.19-21/4.1.7 PKI dir unreadable: ${pki_dir}"
  elif ! _node_priv test -f "${pki_dir}/ca.crt"; then
    fail "CIS 4.1.7 kubelet CA file missing: ${pki_dir}/ca.crt"
  else
    local pki_entries pki_count=0 pki_bad=""
    if ! pki_entries=$(_node_priv find -L "${pki_dir}" \( -type f -o -type d \) 2>/dev/null); then
      fail "CIS 1.1.19-21 PKI scan failed (unreadable entry or broken symlink): ${pki_dir}"
    else
      pki_entries=$(printf '%s\n' "${pki_entries}" | sort)
      while IFS= read -r entry; do
        [ -n "${entry}" ] || continue
        local pinfo pmode powner rel
        pinfo=$(_node_stat "${entry}" || echo "")
        pmode="${pinfo%% *}"
        powner="${pinfo#* }"
        rel="${entry#"${pki_dir}"}"
        [ -n "${rel}" ] || rel="/"
        if [ -z "${pinfo}" ] || ! [[ "${pmode}" =~ ^[0-7]+$ ]] || [ "${powner}" != "${expected_cni_owner}" ]; then
          pki_bad="${pki_bad} ${rel}=${pmode:-?}:${powner:-?}"
          continue
        fi
        case "${entry}" in
          *.crt|*.key)
            pki_count=$((pki_count + 1))
            if [ $((8#${pmode} & 8#7177)) -ne 0 ]; then
              pki_bad="${pki_bad} ${rel}=${pmode}:${powner}"
            fi
            ;;
        esac
      done <<< "${pki_entries}"
      if [ "${pki_count}" -eq 0 ]; then
        fail "CIS 1.1.20-21 PKI certs/keys: none found in ${pki_dir}"
      elif [ -n "${pki_bad}" ]; then
        fail "CIS 1.1.19-21/4.1.7 PKI perms/owner (expected <=600 ${expected_cni_owner}):${pki_bad}"
      else
        pass "CIS 1.1.19-21/4.1.7 PKI hardening: ${pki_count} cert/key file(s) <=600 (${expected_cni_owner})"
      fi
    fi
  fi

  local svc_count=0 svc_bad="" svc_path svc_dir dropin_files
  for svc_path in \
    "${prefix}/usr/lib/systemd/system/kubelet.service" \
    "${prefix}/lib/systemd/system/kubelet.service" \
    "${prefix}/etc/systemd/system/kubelet.service"; do
    if _node_priv test -f "${svc_path}"; then
      svc_count=$((svc_count + 1))
      local sinfo smode sowner
      sinfo=$(_node_stat "${svc_path}" || echo "")
      smode="${sinfo%% *}"
      sowner="${sinfo#* }"
      if [ -z "${sinfo}" ] || ! [[ "${smode}" =~ ^[0-7]+$ ]] || [ $((8#${smode} & 8#7177)) -ne 0 ] || [ "${sowner}" != "${expected_cni_owner}" ]; then
        svc_bad="${svc_bad} ${svc_path#"${prefix}"}=${smode:-?}:${sowner:-?}"
      fi
    fi
  done
  for svc_dir in \
    "${prefix}/usr/lib/systemd/system/kubelet.service.d" \
    "${prefix}/etc/systemd/system/kubelet.service.d"; do
    if _node_priv test -d "${svc_dir}"; then
      if ! dropin_files=$(_node_priv find -L "${svc_dir}" -mindepth 1 -maxdepth 1 -type f 2>/dev/null); then
        svc_bad="${svc_bad} ${svc_dir#"${prefix}"}=unreadable"
      else
        dropin_files=$(printf '%s\n' "${dropin_files}" | sort)
        while IFS= read -r f; do
          [ -n "${f}" ] || continue
          svc_count=$((svc_count + 1))
          local dinfo dmode downer
          dinfo=$(_node_stat "${f}" || echo "")
          dmode="${dinfo%% *}"
          downer="${dinfo#* }"
          if [ -z "${dinfo}" ] || ! [[ "${dmode}" =~ ^[0-7]+$ ]] || [ $((8#${dmode} & 8#7177)) -ne 0 ] || [ "${downer}" != "${expected_cni_owner}" ]; then
            svc_bad="${svc_bad} ${f#"${prefix}"}=${dmode:-?}:${downer:-?}"
          fi
        done <<< "${dropin_files}"
      fi
    fi
  done
  if [ "${svc_count}" -eq 0 ]; then
    fail "CIS 4.1.1-2 kubelet systemd unit/drop-ins: none found"
  elif [ -n "${svc_bad}" ]; then
    fail "CIS 4.1.1-2 kubelet systemd unit/drop-ins (expected <=600 ${expected_cni_owner}):${svc_bad}"
  else
    pass "CIS 4.1.1-2 kubelet systemd unit/drop-ins: ${svc_count} file(s) <=600 (${expected_cni_owner})"
  fi

  local kubelet_cfg="${prefix}/var/lib/kubelet/config.yaml"
  if ! _node_priv test -f "${kubelet_cfg}"; then
    fail "CIS 4.1.9 kubelet config missing: ${kubelet_cfg}"
  else
    local kinfo kmode kowner
    kinfo=$(_node_stat "${kubelet_cfg}" || echo "")
    kmode="${kinfo%% *}"
    kowner="${kinfo#* }"
    if [ -z "${kinfo}" ] || ! [[ "${kmode}" =~ ^[0-7]+$ ]] || [ $((8#${kmode} & 8#7177)) -ne 0 ] || [ "${kowner}" != "${expected_cni_owner}" ]; then
      fail "CIS 4.1.9 kubelet config perms/owner (${kubelet_cfg}): perm=${kmode:-?}:${kowner:-?} (expected <=600 ${expected_cni_owner})"
    else
      pass "CIS 4.1.9 kubelet config perms: <=600 (${expected_cni_owner})"
    fi

    # D-KUBELET-DEFERRED-GAPS: report missing settings directly. The serving TLS
    # rollout verifies the wire separately because serverTLSBootstrap is only
    # a config field. The two other runtime settings still have rollout prerequisites.
    local kbody kgap=""
    if ! kbody=$(_node_priv cat "${kubelet_cfg}" 2>/dev/null); then
      warn "CIS 4.2.6/4.2.12/4.2.13 kubelet config unreadable (known gap; see docs/common/compliance-hardening.md#scan-coverage): ${kubelet_cfg}"
    else
      if ! printf '%s\n' "${kbody}" | grep -Eq '^[[:space:]]*protectKernelDefaults:[[:space:]]*true([[:space:]]*(#.*)?)?$'; then
        kgap="${kgap} protectKernelDefaults!=true(CIS-4.2.6)"
      fi
      if ! printf '%s\n' "${kbody}" | grep -Eq '^[[:space:]]*serverTLSBootstrap:[[:space:]]*true([[:space:]]*(#.*)?)?$'; then
        kgap="${kgap} serverTLSBootstrap!=true(CIS-4.2.12)"
      fi
      local ciphers cipher_count=0 bad_ciphers=""
      ciphers=$(printf '%s\n' "${kbody}" | awk '
        /^[[:space:]]*tlsCipherSuites:[[:space:]]*\[/ {
          sub(/^[^[]*\[/, ""); sub(/\].*$/, "");
          n = split($0, arr, ",");
          for (i = 1; i <= n; i++) {
            gsub(/^[[:space:]"\047]+|[[:space:]"\047]+$/, "", arr[i]);
            if (arr[i] != "") print arr[i];
          }
          next
        }
        /^[[:space:]]*tlsCipherSuites:[[:space:]]*(#.*)?$/ { in_list = 1; next }
        in_list && /^[[:space:]]*-[[:space:]]+/ {
          val = $0;
          sub(/^[[:space:]]*-[[:space:]]+/, "", val);
          sub(/[[:space:]]+#.*$/, "", val);
          gsub(/^[[:space:]"\047]+|[[:space:]"\047]+$/, "", val);
          if (val != "") print val;
          next
        }
        in_list && /^[[:space:]]*(#.*)?$/ { next }
        in_list { in_list = 0 }
      ')
      while IFS= read -r c; do
        [ -n "${c}" ] || continue
        cipher_count=$((cipher_count + 1))
        case "${c}" in
          TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256|\
          TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256|\
          TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384|\
          TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384|\
          TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305|\
          TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305|\
          TLS_RSA_WITH_AES_256_GCM_SHA384|\
          TLS_RSA_WITH_AES_128_GCM_SHA256) ;;
          *) bad_ciphers="${bad_ciphers} ${c}" ;;
        esac
      done <<< "${ciphers}"
      if [ "${cipher_count}" -eq 0 ]; then
        kgap="${kgap} tlsCipherSuites=empty(CIS-4.2.13)"
      elif [ -n "${bad_ciphers}" ]; then
        kgap="${kgap} weakCipher=${bad_ciphers# }(CIS-4.2.13)"
      fi
      if [ -n "${kgap}" ]; then
        warn "CIS 4.2.6/4.2.12/4.2.13 kubelet hardening known gap (see docs/common/compliance-hardening.md#scan-coverage):${kgap}"
      fi
    fi
  fi
}

# Stage → section mapping
PHASE1_SECTIONS="nodes kube-vip etcd cilium core"
PHASE2_INFRA_SECTIONS="nodes network tls dns"
PHASE2_APPS_SECTIONS="nodes database keycloak monitoring platform istio gitops routes podhealth"

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

# Detect execution environment: VM node or remote (Mac/laptop)
IS_CLUSTER_NODE=false
if [ -f /etc/kubernetes/admin.conf ] || systemctl is-active --quiet kubelet 2>/dev/null; then
  IS_CLUSTER_NODE=true
fi

# Check pod status in a namespace by label
check_pods() {
  local ns="$1" label="$2" expected="$3" name="$4"
  local running
  running=$(kubectl get pods -n "${ns}" -l "${label}" --no-headers 2>/dev/null | grep -c "Running" || true)
  running=${running:-0}
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
  ready=$(kubectl get "${kind}" -n "${ns}" "${name}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)
  local desired
  desired=$(kubectl get "${kind}" -n "${ns}" "${name}" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)
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
  echo "--- [1/17] Nodes ---"
  NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
  READY_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || true)
  NOTREADY=$(kubectl get nodes --no-headers 2>/dev/null | grep -v " Ready" || echo "")

  if [ "${NODE_COUNT}" -ge "${TOPOLOGY_NODES}" ]; then
    pass "Node count: ${NODE_COUNT}"
  else
    fail "Node count: ${NODE_COUNT} (expected >= ${TOPOLOGY_NODES})"
  fi

  if [ "${READY_COUNT}" = "${NODE_COUNT}" ]; then
    pass "All nodes Ready: ${READY_COUNT}/${NODE_COUNT}"
  else
    fail "Nodes NotReady: ${NOTREADY}"
  fi

  # Check master nodes have control-plane role
  MASTER_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | grep -c "control-plane" || true)
  if [ "${MASTER_COUNT}" -ge "${TOPOLOGY_MASTERS}" ]; then
    pass "Control plane nodes: ${MASTER_COUNT}"
  else
    fail "Control plane nodes: ${MASTER_COUNT} (expected >= ${TOPOLOGY_MASTERS})"
  fi

  # Check control-plane taint (should be NoSchedule after Phase 2)
  TAINTED_MASTERS=0
  for node in $(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null); do
    if kubectl describe node "${node}" 2>/dev/null | grep -q "node-role.kubernetes.io/control-plane:NoSchedule"; then
      TAINTED_MASTERS=$((TAINTED_MASTERS + 1))
    fi
  done
  if [ "${NARWHAL_TOPOLOGY:-ha}" = "single" ]; then
    if EXCLUDED_NODES=$(kubectl get nodes -l node.kubernetes.io/exclude-from-external-load-balancers -o name 2>/dev/null) && [ -z "${EXCLUDED_NODES}" ]; then
      pass "Single node eligible for MetalLB announcements"
    else
      fail "Single node LB exclusion label present or node query failed: ${EXCLUDED_NODES}"
    fi
    if [ "${TAINTED_MASTERS}" -eq 0 ]; then
      pass "Single control plane schedulable"
    else
      fail "Single control plane has NoSchedule taint"
    fi
  elif [ "${TAINTED_MASTERS}" -ge "${MASTER_COUNT}" ]; then
    pass "Control-plane taint: ${TAINTED_MASTERS}/${MASTER_COUNT} masters tainted"
  elif [ "${TAINTED_MASTERS}" -ge 1 ]; then
    warn "Control-plane taint: ${TAINTED_MASTERS}/${MASTER_COUNT} masters tainted"
  else
    warn "Control-plane taint: not applied (Phase 1 or taint removed)"
  fi

  if ${IS_CLUSTER_NODE}; then
    verify_node_file_hardening
  else
    skip "CIS 1.1.9/1.1.12 node file hardening: requires local execution on cluster node"
  fi
  echo ""
fi

#=========================================
# 2. KUBE-VIP & VIP
#=========================================
if should_run "kube-vip"; then
  echo "--- [2/17] kube-vip & VIP ---"
  if [ "${PROVIDER}" = "vagrant" ]; then
    KVIP_PODS=$(kubectl get pods -n kube-system -l k8s-app=kube-vip --no-headers 2>/dev/null | grep -c "Running" || true)
    if [ "${KVIP_PODS}" -ge "${TOPOLOGY_MASTERS}" ]; then
      pass "kube-vip pods: ${KVIP_PODS} Running"
    elif [ "${KVIP_PODS}" -ge 1 ]; then
      warn "kube-vip pods: ${KVIP_PODS} Running (expected ${TOPOLOGY_MASTERS} for ${NARWHAL_TOPOLOGY:-ha})"
    else
      fail "kube-vip pods: ${KVIP_PODS} Running"
    fi

    if ping -c 1 -W 2 192.168.56.100 &>/dev/null; then
      pass "VIP 192.168.56.100: reachable"
    else
      fail "VIP 192.168.56.100: unreachable"
    fi

    # VIP responds to K8s API
    if kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes &>/dev/null 2>&1; then
      pass "VIP API server: responding"
    else
      warn "VIP API server: not responding (using config-local)"
    fi
  else
    skip "kube-vip pods: Vagrant-only (PROVIDER=${PROVIDER})"
    skip "VIP 192.168.56.100: Vagrant-only (PROVIDER=${PROVIDER})"
  fi

  echo ""
fi

#=========================================
# 3. ETCD
#=========================================
if should_run "etcd"; then
  echo "--- [3/17] etcd ---"
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
  if [ "${ETCD_MEMBERS}" -ge "${TOPOLOGY_MASTERS}" ]; then
    pass "etcd members: ${ETCD_MEMBERS}"
  else
    fail "etcd members: ${ETCD_MEMBERS} (expected >= ${TOPOLOGY_MASTERS})"
  fi

  # etcd health check
  if [ -n "${ETCD_POD}" ]; then
    ETCD_HEALTH=$(kubectl exec -n kube-system "${ETCD_POD}" -- \
      etcdctl endpoint health \
      --endpoints=https://127.0.0.1:2379 \
      --cacert=/etc/kubernetes/pki/etcd/ca.crt \
      --cert=/etc/kubernetes/pki/etcd/server.crt \
      --key=/etc/kubernetes/pki/etcd/server.key 2>/dev/null | grep -c "is healthy" || true)
    if [ "${ETCD_HEALTH}" -ge 1 ]; then
      pass "etcd health: healthy"
    else
      fail "etcd health: unhealthy"
    fi
  fi
  echo ""
fi

#=========================================
# 4. CNI (Cilium)
#=========================================
if should_run "cilium"; then
  echo "--- [4/17] Cilium CNI ---"
  check_pods "kube-system" "k8s-app=cilium" "${TOPOLOGY_NODES}" "cilium agents"
  CILIUM_OP_NAME=$(kubectl get deployment -n kube-system -l app.kubernetes.io/name=cilium-operator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null \
    || kubectl get deployment -n kube-system -l name=cilium-operator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null \
    || echo "cilium-operator")
  check_ready "deployment" "kube-system" "${CILIUM_OP_NAME}" "cilium-operator"

  # kube-proxy replacement
  KPR=$(kubectl get cm cilium-config -n kube-system -o jsonpath='{.data.kube-proxy-replacement}' 2>/dev/null || echo "")
  if [ "${KPR}" = "true" ]; then
    pass "Cilium kubeProxyReplacement: true"
  else
    warn "Cilium kubeProxyReplacement: ${KPR:-not set}"
  fi

  # Cilium cni.exclusive (must be false for Istio CNI coexistence)
  CNI_EXCLUSIVE=$(kubectl get cm cilium-config -n kube-system -o jsonpath='{.data.cni-exclusive}' 2>/dev/null || echo "")
  if [ "${CNI_EXCLUSIVE}" = "false" ]; then
    pass "Cilium cni.exclusive: false (Istio CNI coexistence)"
  else
    fail "Cilium cni.exclusive: ${CNI_EXCLUSIVE:-not set} (must be false for Istio)"
  fi

  # Cilium socketLB.hostNamespaceOnly (must be true to prevent ztunnel bypass)
  SOCKET_LB_HOST=$(kubectl get cm cilium-config -n kube-system -o jsonpath='{.data.bpf-lb-sock-hostns-only}' 2>/dev/null || echo "")
  if [ "${SOCKET_LB_HOST}" = "true" ]; then
    pass "Cilium socketLB.hostNamespaceOnly: true"
  else
    fail "Cilium socketLB.hostNamespaceOnly: ${SOCKET_LB_HOST:-not set} (must be true for Istio)"
  fi

  if ! ${QUICK_MODE}; then
    HUBBLE_RELAY=$(kubectl get pods -n kube-system -l k8s-app=hubble-relay --no-headers 2>/dev/null | grep -c "Running" || true)
    if [ "${HUBBLE_RELAY}" -ge 1 ]; then
      pass "hubble-relay: Running"
    else
      warn "hubble-relay: not running (optional)"
    fi

    # Hubble UI
    HUBBLE_UI=$(kubectl get pods -n kube-system -l k8s-app=hubble-ui --no-headers 2>/dev/null | grep -c "Running" || true)
    if [ "${HUBBLE_UI}" -ge 1 ]; then
      pass "hubble-ui: Running"
    else
      warn "hubble-ui: not running (optional)"
    fi
  fi
  echo ""
fi

#=========================================
# 5. CORE SERVICES (CoreDNS, metrics-server, NFS)
#=========================================
if should_run "core"; then
  echo "--- [5/17] Core Services ---"
  check_ready "deployment" "kube-system" "coredns" "CoreDNS"

  # CoreDNS forward rule for ${DOMAIN}
  COREDNS_FWD=$(kubectl get configmap coredns -n kube-system -o yaml 2>/dev/null | grep -c "${DOMAIN}" || true)
  if [ "${COREDNS_FWD}" -gt 0 ]; then
    pass "CoreDNS forward rule: ${DOMAIN} configured"
  else
    fail "CoreDNS forward rule: ${DOMAIN} MISSING"
  fi

  check_ready "deployment" "kube-system" "metrics-server" "metrics-server"
  check_ready "deployment" "kube-system" "csi-nfs-controller" "csi-nfs-controller"

  # CSI NFS node agents
  CSI_NFS_NODES=$(kubectl get pods -n kube-system -l app=csi-nfs-node --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${CSI_NFS_NODES}" -ge 1 ]; then
    pass "csi-nfs-node: ${CSI_NFS_NODES} Running"
  else
    warn "csi-nfs-node: not running"
  fi

  # StorageClass nfs-csi existence
  SC_NFS=$(kubectl get storageclass nfs-csi -o jsonpath='{.metadata.name}' 2>/dev/null || echo "")
  if [ "${SC_NFS}" = "nfs-csi" ]; then
    pass "StorageClass nfs-csi: exists"
  else
    fail "StorageClass nfs-csi: not found"
  fi

  # NFS server (systemd on master-1, requires local execution)
  if ${IS_CLUSTER_NODE}; then
    if systemctl is-active --quiet nfs-kernel-server 2>/dev/null; then
      pass "NFS server: active"
    elif systemctl is-active --quiet nfs-server 2>/dev/null; then
      pass "NFS server: active"
    else
      fail "NFS server: not active"
    fi

    # NFS exports
    NFS_EXPORTS=$(sudo exportfs -s 2>/dev/null | wc -l | tr -d ' ' || true)
    NFS_EXPORTS=${NFS_EXPORTS:-0}
    if [ "${NFS_EXPORTS}" -ge 1 ]; then
      pass "NFS exports: ${NFS_EXPORTS} active"
    else
      fail "NFS exports: none configured"
    fi
  else
    # Remote: check NFS via PV/PVC
    NFS_PV=$(kubectl get pv 2>/dev/null | grep -c "nfs" || true)
    NFS_PV=${NFS_PV:-0}
    if [ "${NFS_PV}" -ge 1 ]; then
      pass "NFS server: ${NFS_PV} NFS PersistentVolumes detected (remote check)"
    else
      warn "NFS server: cannot verify (run on master-1 for systemd checks)"
    fi
  fi

  # NFS quota agent (runs in nfs-quota-agent namespace)
  NFS_AGENT=$(kubectl get pods -n nfs-quota-agent --no-headers 2>/dev/null | grep -c "Running" || true)
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
  echo "--- [6/17] Database (CNPG) ---"
  CNPG_OP=$(kubectl get pods -n platform-system -l app.kubernetes.io/name=cloudnative-pg --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${CNPG_OP}" -ge 1 ]; then
    pass "CNPG operator: Running"
  else
    fail "CNPG operator: not running"
  fi

  DB_STATUS=$(kubectl get cluster narwhal-db -n database -o jsonpath='{.status.phase}' 2>/dev/null | tr -d '\n' || echo "UNKNOWN")
  DB_READY=$(kubectl get cluster narwhal-db -n database -o jsonpath='{.status.readyInstances}' 2>/dev/null | tr -d '\n' || true)
  DB_INSTANCES=$(kubectl get cluster narwhal-db -n database -o jsonpath='{.spec.instances}' 2>/dev/null | tr -d '\n' || true)
  if [ "${DB_STATUS}" = "Cluster in healthy state" ] || [ "${DB_READY}" = "${DB_INSTANCES}" ]; then
    pass "narwhal-db: ${DB_READY}/${DB_INSTANCES} instances (${DB_STATUS})"
  else
    fail "narwhal-db: ${DB_READY}/${DB_INSTANCES} instances (${DB_STATUS})"
  fi

  # Pooler
  POOLER=$(kubectl get pods -n database -l cnpg.io/poolerName=narwhal-db-pooler-rw --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${POOLER}" -ge 1 ]; then
    pass "narwhal-db-pooler-rw: Running"
  else
    warn "narwhal-db-pooler-rw: not running"
  fi

  # Individual databases existence (keycloak, harbor, gitea)
  if ! ${QUICK_MODE}; then
    DB_PRIMARY=$(kubectl get pods -n database -l cnpg.io/cluster=narwhal-db,role=primary -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [ -n "${DB_PRIMARY}" ]; then
      for db_name in keycloak harbor gitea; do
        DB_EXISTS=$(kubectl exec -n database "${DB_PRIMARY}" -- psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${db_name}'" 2>/dev/null | tr -d ' \n' || echo "")
        if [ "${DB_EXISTS}" = "1" ]; then
          pass "Database '${db_name}': exists"
        else
          fail "Database '${db_name}': not found"
        fi
      done
    else
      warn "Database check: primary pod not found, skipping individual DB checks"
    fi
  fi

  # Consumers connect directly to the CNPG-owned read-write Service.
  DB_SVC_TYPE=$(kubectl get svc narwhal-db-rw -n database -o jsonpath='{.spec.type}' 2>/dev/null || echo "")
  DB_ENDPOINTS=$(kubectl get endpointslice -n database -l kubernetes.io/service-name=narwhal-db-rw -o jsonpath='{.items[*].endpoints[?(@.conditions.ready==true)].addresses[*]}' 2>/dev/null || echo "")
  if [ "${DB_SVC_TYPE}" = "ClusterIP" ] && [ -n "${DB_ENDPOINTS}" ]; then
    pass "Direct DB service narwhal-db-rw.database.svc.cluster.local: ready endpoints"
  else
    fail "Direct DB service: type=${DB_SVC_TYPE:-not found}, ready endpoints=${DB_ENDPOINTS:-none}"
  fi
  echo ""
fi

#=========================================
# 7. METALLB & APISIX
#=========================================
if should_run "network"; then
  echo "--- [7/17] MetalLB & APISIX ---"
  if [ "${PROVIDER}" = "vagrant" ]; then
    check_ready "deployment" "platform-system" "metallb-controller" "MetalLB controller"

    METALLB_SPEAKERS=$(kubectl get pods -n platform-system -l app.kubernetes.io/component=speaker --no-headers 2>/dev/null | grep -c "Running" || true)
    if [ "${METALLB_SPEAKERS}" -ge 1 ]; then
      pass "MetalLB speakers: ${METALLB_SPEAKERS} Running"
    else
      fail "MetalLB speakers: not running"
    fi

    # MetalLB IPAddressPool
    POOL_EXISTS=$(kubectl get ipaddresspool default-pool -n platform-system -o jsonpath='{.metadata.name}' 2>/dev/null || echo "")
    if [ "${POOL_EXISTS}" = "default-pool" ]; then
      pass "MetalLB IPAddressPool: default-pool exists"
    else
      fail "MetalLB IPAddressPool: default-pool not found"
    fi

    # MetalLB L2Advertisement
    L2_EXISTS=$(kubectl get l2advertisement default -n platform-system -o jsonpath='{.metadata.name}' 2>/dev/null || echo "")
    if [ "${L2_EXISTS}" = "default" ]; then
      pass "MetalLB L2Advertisement: exists"
    else
      fail "MetalLB L2Advertisement: not found"
    fi
  else
    skip "MetalLB controller: Vagrant-only (PROVIDER=${PROVIDER})"
    skip "MetalLB speakers: Vagrant-only (PROVIDER=${PROVIDER})"
    skip "MetalLB IPAddressPool: Vagrant-only (PROVIDER=${PROVIDER})"
    skip "MetalLB L2Advertisement: Vagrant-only (PROVIDER=${PROVIDER})"
  fi

  # APISIX gateway deployment (label-based, name is helm-rendered)
  APISIX_GW_READY=$(kubectl get deploy -n platform-system -l app.kubernetes.io/name=apisix \
    -o jsonpath='{range .items[*]}{.status.readyReplicas}{"\t"}{.spec.replicas}{"\n"}{end}' 2>/dev/null | head -1 || echo "")
  APISIX_GW_R=$(echo "${APISIX_GW_READY}" | awk '{print $1}')
  APISIX_GW_D=$(echo "${APISIX_GW_READY}" | awk '{print $2}')
  if [ "${APISIX_GW_R:-0}" -gt 0 ] && [ "${APISIX_GW_R}" = "${APISIX_GW_D}" ]; then
    pass "APISIX gateway: ${APISIX_GW_R}/${APISIX_GW_D} Ready"
  else
    fail "APISIX gateway: ${APISIX_GW_R:-0}/${APISIX_GW_D:-0} Ready"
  fi

  # APISIX Ingress Controller deployment (label-based)
  APISIX_IC_READY=$(kubectl get deploy -n platform-system -l app.kubernetes.io/name=apisix-ingress-controller \
    -o jsonpath='{range .items[*]}{.status.readyReplicas}{"\t"}{.spec.replicas}{"\n"}{end}' 2>/dev/null | head -1 || echo "")
  APISIX_IC_R=$(echo "${APISIX_IC_READY}" | awk '{print $1}')
  APISIX_IC_D=$(echo "${APISIX_IC_READY}" | awk '{print $2}')
  if [ "${APISIX_IC_R:-0}" -gt 0 ] && [ "${APISIX_IC_R}" = "${APISIX_IC_D}" ]; then
    pass "APISIX Ingress Controller: ${APISIX_IC_R}/${APISIX_IC_D} Ready"
  else
    fail "APISIX Ingress Controller: ${APISIX_IC_R:-0}/${APISIX_IC_D:-0} Ready"
  fi

  if [ "${PROVIDER}" = "vagrant" ]; then
    # APISIX gateway LoadBalancer IP (resilient: query by type, no hardcoded svc name)
    APISIX_LB_IP=$(kubectl get svc -n platform-system \
      -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.status.loadBalancer.ingress[0].ip}{"\n"}{end}' \
      2>/dev/null | grep -v '^$' | head -1 || echo "")
    if [ "${APISIX_LB_IP}" = "192.168.56.200" ]; then
      pass "APISIX LoadBalancer IP: ${APISIX_LB_IP}"
    elif [ -n "${APISIX_LB_IP}" ]; then
      warn "APISIX LoadBalancer IP: ${APISIX_LB_IP} (expected 192.168.56.200)"
    else
      fail "APISIX LoadBalancer IP: not assigned"
    fi
  else
    skip "APISIX LoadBalancer IP: Vagrant-only (PROVIDER=${PROVIDER})"
  fi

  # ApisixRoute CRD presence and route count
  APISIX_ROUTE_COUNT=$(kubectl get apisixroutes.apisix.apache.org -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
  if [ "${APISIX_ROUTE_COUNT:-0}" -gt 0 ]; then
    pass "ApisixRoutes: ${APISIX_ROUTE_COUNT} routes defined"
  else
    fail "ApisixRoutes: none found (CRD missing or no routes)"
  fi
  echo ""
fi

#=========================================
# 8. CERT-MANAGER & TLS
#=========================================
if should_run "tls"; then
  echo "--- [8/17] cert-manager & TLS ---"
  check_ready "deployment" "platform-system" "cert-manager" "cert-manager"
  check_ready "deployment" "platform-system" "cert-manager-webhook" "cert-manager-webhook"
  check_ready "deployment" "platform-system" "cert-manager-cainjector" "cert-manager-cainjector"

  # ClusterIssuer
  ISSUER_READY=$(kubectl get clusterissuer selfsigned-cluster-issuer -o jsonpath='{.status.conditions[0].status}' 2>/dev/null || echo "")
  if [ "${ISSUER_READY}" = "True" ]; then
    pass "ClusterIssuer selfsigned: Ready"
  else
    fail "ClusterIssuer selfsigned: ${ISSUER_READY:-not found}"
  fi

  # TLS Certificate for *.${DOMAIN}
  CERT_READY=$(kubectl get certificate narwhal-wildcard-tls -n platform-system -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
  if [ "${CERT_READY}" = "True" ]; then
    pass "TLS cert *.${DOMAIN}: Ready"
  else
    fail "TLS cert *.${DOMAIN}: ${CERT_READY:-not found}"
  fi

  # Certificate covers wildcard domain
  CERT_DOMAINS=$(kubectl get certificate narwhal-wildcard-tls -n platform-system -o jsonpath='{.spec.dnsNames[*]}' 2>/dev/null || echo "")
  if echo "${CERT_DOMAINS}" | grep -F -- "*.${DOMAIN}" >/dev/null; then
    pass "TLS cert wildcard: *.${DOMAIN} included"
  else
    fail "TLS cert wildcard: *.${DOMAIN} not found in [${CERT_DOMAINS}]"
  fi

  # CA cert distribution to namespaces that must trust the internal CA
  # (narwhal-ca-cert is synced to these via GitOps; old headlamp/gitea/harbor/
  #  oauth2-proxy namespaces were consolidated into devtools / removed)
  CA_DIST_OK=0
  CA_DIST_FAIL=0
  for ns in devtools iam monitoring storage; do
    CA_SECRET=$(kubectl get secret narwhal-ca-cert -n "${ns}" -o jsonpath='{.data.ca\.crt}' 2>/dev/null || echo "")
    if [ -n "${CA_SECRET}" ]; then
      CA_DIST_OK=$((CA_DIST_OK + 1))
    else
      CA_DIST_FAIL=$((CA_DIST_FAIL + 1))
    fi
  done
  if [ "${CA_DIST_FAIL}" -eq 0 ]; then
    pass "CA cert distribution: ${CA_DIST_OK}/4 CA-trust namespaces"
  else
    fail "CA cert distribution: ${CA_DIST_OK}/4 CA-trust namespaces (${CA_DIST_FAIL} missing)"
  fi
  echo ""
fi

#=========================================
# 9. DNS
#=========================================
if should_run "dns"; then
  echo "--- [9/17] DNS ---"
  if [ "${PROVIDER}" = "vagrant" ]; then
    # dnsmasq service (requires local execution on master node)
    if ${IS_CLUSTER_NODE}; then
      if systemctl is-active --quiet dnsmasq 2>/dev/null; then
        pass "dnsmasq: active"
      else
        fail "dnsmasq: not active"
      fi

      # Host DNS resolution (local dnsmasq)
      HOST_DNS=$(nslookup keycloak.${DOMAIN} 127.0.0.1 2>/dev/null | grep -c "192.168.56.200" || true)
      HOST_DNS=${HOST_DNS:-0}
      if [ "${HOST_DNS}" -gt 0 ]; then
        pass "Host DNS: keycloak.${DOMAIN} -> 192.168.56.200"
      else
        fail "Host DNS: keycloak.${DOMAIN} resolution failed"
      fi
    else
      # Remote: check dnsmasq via master node DNS resolution
      MASTER_IP=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "")
      if [ -n "${MASTER_IP}" ]; then
        REMOTE_DNS=$(nslookup keycloak.${DOMAIN} "${MASTER_IP}" 2>/dev/null | grep -c "192.168.56.200" || true)
        REMOTE_DNS=${REMOTE_DNS:-0}
        if [ "${REMOTE_DNS}" -gt 0 ]; then
          pass "dnsmasq: resolving via master ${MASTER_IP}"
        else
          fail "dnsmasq: keycloak.${DOMAIN} not resolving via ${MASTER_IP}"
        fi
      else
        warn "dnsmasq: cannot verify (run on master-1 for systemd checks)"
      fi
    fi

    # Pod DNS (only if not quick mode)
    if ! ${QUICK_MODE}; then
      POD_DNS=$(kubectl run verify-dns-$$ --rm -i --restart=Never --image=busybox:1.36 -- nslookup keycloak.${DOMAIN} 2>/dev/null | grep -c "192.168.56.200" | tr -d '\n' || true)
      POD_DNS=${POD_DNS:-0}
      if [ "${POD_DNS}" -gt 0 ]; then
        pass "Pod DNS: keycloak.${DOMAIN} -> 192.168.56.200"
      else
        fail "Pod DNS: keycloak.${DOMAIN} resolution failed (CoreDNS forward rule?)"
      fi

      # Worker node DNS: schedule a Pod on a worker to verify *.${DOMAIN} resolves
      WORKER_NODE=$(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
      if [ -n "${WORKER_NODE}" ]; then
        WORKER_DNS=$(kubectl run verify-worker-dns-$$ --rm -i --restart=Never \
          --overrides='{"spec":{"nodeName":"'"${WORKER_NODE}"'"}}' \
          --image=busybox:1.36 -- nslookup keycloak.${DOMAIN} 2>/dev/null | grep -c "192.168.56.200" | tr -d '\n' || true)
        WORKER_DNS=${WORKER_DNS:-0}
        if [ "${WORKER_DNS}" -gt 0 ]; then
          pass "Worker DNS (${WORKER_NODE}): keycloak.${DOMAIN} -> 192.168.56.200"
        else
          fail "Worker DNS (${WORKER_NODE}): keycloak.${DOMAIN} resolution failed"
        fi
      elif [ "${NARWHAL_TOPOLOGY}" = single ]; then
        skip "Worker DNS: single topology has no worker node (Pod DNS checked above)"
      else
        warn "Worker DNS: no worker node found, skipping"
      fi
    fi
  else
    skip "dnsmasq / host DNS on master: Vagrant-only (PROVIDER=${PROVIDER})"
    skip "Pod DNS -> 192.168.56.200: Vagrant-only (PROVIDER=${PROVIDER})"
    skip "Worker DNS -> 192.168.56.200: Vagrant-only (PROVIDER=${PROVIDER})"
  fi

  echo ""
fi

#=========================================
# 10. KEYCLOAK & OIDC
#=========================================
if should_run "keycloak"; then
  echo "--- [10/17] Keycloak & OIDC ---"
  # Keycloak operator
  check_ready "deployment" "iam" "keycloak-operator" "Keycloak operator"

  # Keycloak instance (StatefulSet managed by operator, pod label app=keycloak)
  KC_PODS=$(kubectl get pods -n iam -l app=keycloak --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${KC_PODS}" -ge 1 ]; then
    pass "Keycloak: ${KC_PODS} Running"
  else
    fail "Keycloak: not running"
  fi

  # OIDC HTTPS endpoint
  OIDC_CODE=$(curl -sk -o /dev/null -w '%{http_code}' https://keycloak.${DOMAIN}/realms/narwhal/.well-known/openid-configuration 2>/dev/null || echo "000")
  if [ "${OIDC_CODE}" = "200" ]; then
    pass "OIDC HTTPS endpoint: HTTP ${OIDC_CODE}"
  else
    fail "OIDC HTTPS endpoint: HTTP ${OIDC_CODE} (expected 200)"
  fi

  # API server OIDC flags on every kube-apiserver pod (D-APISERVER-OIDC-ALL)
  # One kube-apiserver mirror pod per control-plane node: a master with no mirror pod must FAIL,
  # not be skipped by the per-pod loop below.
  CP_NODE_COUNT=$(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | grep -c . || true)
  APISERVER_POD_LINES=$(kubectl get pods -n kube-system -l component=kube-apiserver \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].command}{"\n"}{end}' 2>/dev/null || true)
  if [ -z "${APISERVER_POD_LINES}" ]; then
    fail "API server OIDC: no kube-apiserver pods found"
  else
    APISERVER_POD_COUNT=0
    while IFS=$'\t' read -r pod_name pod_cmd; do
      [ -n "${pod_name}" ] || continue
      APISERVER_POD_COUNT=$((APISERVER_POD_COUNT + 1))
      missing_flags=""
      for flag in "oidc-issuer-url=https://keycloak.${DOMAIN}/realms/narwhal" \
                  "oidc-client-id=kubernetes" \
                  "oidc-username-claim=preferred_username" \
                  "oidc-groups-claim=groups" \
                  "oidc-username-prefix=oidc:" \
                  "oidc-groups-prefix=oidc:" \
                  "oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt"; do
        if ! grep -qF -- "\"--${flag}\"" <<<"${pod_cmd}"; then
          missing_flags="${missing_flags} --${flag}"
        fi
      done
      if [ -z "${missing_flags}" ]; then
        pass "API server OIDC (${pod_name}): all 7 --oidc-* flags configured"
      else
        fail "API server OIDC (${pod_name}): missing${missing_flags}"
      fi
    done <<< "${APISERVER_POD_LINES}"
    if [ "${APISERVER_POD_COUNT}" -eq "${CP_NODE_COUNT}" ]; then
      pass "API server OIDC: ${APISERVER_POD_COUNT} kube-apiserver pod(s) for ${CP_NODE_COUNT} control-plane node(s)"
    else
      fail "API server OIDC: ${APISERVER_POD_COUNT} kube-apiserver pod(s) for ${CP_NODE_COUNT} control-plane node(s) (one per node expected)"
    fi
  fi

  # Upgrade safety (D-KUBEADM-CONFIG-OIDC, #505): kubeadm regenerates the static pod from the
  # kubeadm-config ClusterConfiguration, so it must carry the same 7 flags as the running pods.
  # The ConfigMap is compared with the expected flag set (the running pods were checked above).
  KC_ERR=""
  KC_CC=$(kubectl -n kube-system get cm kubeadm-config -o jsonpath='{.data.ClusterConfiguration}' 2>/tmp/verify-kc-err.$$) || KC_ERR=$(head -c 200 /tmp/verify-kc-err.$$ 2>/dev/null || true)
  rm -f /tmp/verify-kc-err.$$
  if ! command -v yq >/dev/null 2>&1; then
    skip "kubeadm-config OIDC: yq not installed, ClusterConfiguration not inspected"
  elif [ -n "${KC_ERR}" ]; then
    skip "kubeadm-config OIDC: cannot read kube-system/kubeadm-config (RBAC or API error: ${KC_ERR})"
  elif [ -z "${KC_CC}" ]; then
    skip "kubeadm-config OIDC: kube-system/kubeadm-config has no data.ClusterConfiguration"
  else
    KC_API=$(printf '%s' "${KC_CC}" | yq '.apiVersion' 2>/dev/null || true)
    KC_OIDC=""
    case "${KC_API}" in
      kubeadm.k8s.io/v1beta4)
        KC_OIDC=$(printf '%s' "${KC_CC}" | yq '(.apiServer.extraArgs // []) | .[] | select(.name | test("^oidc-")) | .name + "=" + (.value | tostring)' 2>/dev/null | LC_ALL=C sort || true) ;;
      kubeadm.k8s.io/v1beta3)
        KC_OIDC=$(printf '%s' "${KC_CC}" | yq '(.apiServer.extraArgs // {}) | to_entries | .[] | select(.key | test("^oidc-")) | .key + "=" + (.value | tostring)' 2>/dev/null | LC_ALL=C sort || true) ;;
      *) KC_API="" ;;
    esac
    KC_WANT=$(printf '%s\n' \
      "oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt" \
      "oidc-client-id=kubernetes" \
      "oidc-groups-claim=groups" \
      "oidc-groups-prefix=oidc:" \
      "oidc-issuer-url=https://keycloak.${DOMAIN}/realms/narwhal" \
      "oidc-username-claim=preferred_username" \
      "oidc-username-prefix=oidc:" | LC_ALL=C sort)
    if [ -z "${KC_API}" ]; then
      skip "kubeadm-config OIDC: unknown ClusterConfiguration apiVersion, not compared"
    elif [ "${KC_OIDC}" = "${KC_WANT}" ]; then
      pass "kubeadm-config OIDC: ClusterConfiguration carries the same 7 --oidc-* flags (survives kubeadm upgrade)"
    else
      fail "kubeadm-config OIDC: ClusterConfiguration apiServer.extraArgs lacks or differs on the 7 --oidc-* flags; kubeadm upgrade would drop OIDC. Fix: sudo bash scripts/cluster/11-4c-kubeadm-config-oidc.sh https://keycloak.${DOMAIN}/realms/narwhal"
    fi
  fi

  # Kubernetes realm existence (check via OIDC well-known)
  if [ "${OIDC_CODE}" = "200" ]; then
    pass "Keycloak realm 'narwhal': reachable (ns=iam)"
  else
    fail "Keycloak realm 'narwhal': not reachable"
  fi

  # groups claim (not in quick mode). Every realm client has
  # directAccessGrantsEnabled=false, so a password grant cannot mint a user token;
  # Keycloak's admin "evaluate scopes" endpoint builds the token the kubernetes
  # client would issue to admin, without a login. kcadm runs inside the pod, so
  # this works from a workstation too.
  if ! ${QUICK_MODE}; then
    # D-KC-ADMIN-573: permanent admin when 11-1 has created it, else the bootstrap admin.
    KC_ADMIN_SECRET=keycloak-initial-admin
    kubectl get secret keycloak-admin -n iam >/dev/null 2>&1 && KC_ADMIN_SECRET=keycloak-admin
    KC_ADMIN_USER=$(kubectl get secret "${KC_ADMIN_SECRET}" -n iam -o jsonpath='{.data.username}' 2>/dev/null | base64 -d || echo "")
    KC_ADMIN_PASS=$(kubectl get secret "${KC_ADMIN_SECRET}" -n iam -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo "")
    kcadm() { kubectl exec -n iam keycloak-0 -c keycloak -- /opt/keycloak/bin/kcadm.sh "$@" 2>/dev/null; }
    first_id() { python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["id"] if d else "")' 2>/dev/null || echo ""; }
    GROUPS_CLAIM=""
    if [ -n "${KC_ADMIN_PASS}" ] && kcadm config credentials --server http://localhost:8080 \
         --realm master --user "${KC_ADMIN_USER}" --password "${KC_ADMIN_PASS}" >/dev/null; then
      K8S_CLIENT_UUID=$(kcadm get clients -r narwhal -q clientId=kubernetes --fields id | first_id)
      KC_ADMIN_UID=$(kcadm get users -r narwhal -q username=admin -q exact=true --fields id | first_id)
      if [ -n "${K8S_CLIENT_UUID}" ] && [ -n "${KC_ADMIN_UID}" ]; then
        GROUPS_CLAIM=$(kcadm get "clients/${K8S_CLIENT_UUID}/evaluate-scopes/generate-example-access-token" \
          -r narwhal -q "userId=${KC_ADMIN_UID}" -q scope=openid \
          | python3 -c 'import sys,json; print(",".join(json.load(sys.stdin).get("groups",[])))' 2>/dev/null || echo "")
        if echo "${GROUPS_CLAIM}" | grep -qw "cluster-admin"; then
          pass "OIDC groups claim: admin has cluster-admin (kubernetes client)"
        else
          fail "OIDC groups claim: cluster-admin not found in [${GROUPS_CLAIM}] (groups client scope missing?)"
        fi
      else
        fail "OIDC groups claim: kubernetes client or admin user missing in realm 'narwhal'"
      fi
    else
      fail "OIDC groups claim: Keycloak admin CLI login failed"
    fi
  fi

  # RBAC ClusterRoleBindings for OIDC groups
  # shellcheck disable=SC2043  # single-item loop kept intentionally for easy extension
  for crb in oidc-cluster-admin; do
    CRB_EXISTS=$(kubectl get clusterrolebinding "${crb}" -o jsonpath='{.metadata.name}' 2>/dev/null || echo "")
    if [ "${CRB_EXISTS}" = "${crb}" ]; then
      pass "ClusterRoleBinding ${crb}: exists"
    else
      fail "ClusterRoleBinding ${crb}: not found"
    fi
  done
  echo ""
fi

#=========================================
# 11. MONITORING (Prometheus, Grafana, Loki, Tempo)
#=========================================
if should_run "monitoring"; then
  echo "--- [11/17] Monitoring ---"
  # Prometheus operator
  PROM_OP=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus-operator --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${PROM_OP}" -ge 1 ]; then
    pass "Prometheus operator: Running"
  else
    # Some chart versions use different label
    PROM_OP2=$(kubectl get pods -n monitoring -l app=kube-prometheus-stack-operator --no-headers 2>/dev/null | grep -c "Running" || true)
    if [ "${PROM_OP2}" -ge 1 ]; then
      pass "Prometheus operator: Running"
    else
      fail "Prometheus operator: not running"
    fi
  fi

  # Prometheus
  PROM_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${PROM_PODS}" -ge 1 ]; then
    pass "Prometheus: Running"
  else
    fail "Prometheus: not running"
  fi

  # Grafana
  check_ready "deployment" "monitoring" "prometheus-stack-grafana" "Grafana"

  # Alertmanager
  ALERT_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=alertmanager --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${ALERT_PODS}" -ge 1 ]; then
    pass "Alertmanager: Running"
  else
    warn "Alertmanager: not running"
  fi

  # kube-state-metrics
  KSM_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=kube-state-metrics --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${KSM_PODS}" -ge 1 ]; then
    pass "kube-state-metrics: Running"
  else
    fail "kube-state-metrics: not running"
  fi

  # node-exporter DaemonSet
  NE_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus-node-exporter --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${NE_PODS}" -ge "${TOPOLOGY_NODES}" ]; then
    pass "node-exporter: ${NE_PODS} Running"
  elif [ "${NE_PODS}" -ge 1 ]; then
    warn "node-exporter: ${NE_PODS} Running (expected >= ${TOPOLOGY_NODES})"
  else
    fail "node-exporter: not running"
  fi

  # Loki
  LOKI_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=loki --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${LOKI_PODS}" -ge 1 ]; then
    pass "Loki: Running"
  else
    fail "Loki: not running"
  fi

  # Grafana Alloy (k8s-monitoring) — replaces Promtail (EOL 2026-03-02)
  ALLOY_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=alloy-logs --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${ALLOY_PODS}" -ge 1 ]; then
    pass "Alloy: ${ALLOY_PODS} Running"
  else
    fail "Alloy: not running"
  fi

  # Tempo
  TEMPO_PODS=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=tempo --no-headers 2>/dev/null | grep -c "Running" || true)
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
  echo "--- [12/17] Platform Apps ---"
  # Kyverno (namespace: platform-system)
  check_ready "deployment" "platform-system" "kyverno-admission-controller" "Kyverno admission"
  check_ready "deployment" "platform-system" "kyverno-background-controller" "Kyverno background"
  check_ready "deployment" "platform-system" "kyverno-cleanup-controller" "Kyverno cleanup"
  check_ready "deployment" "platform-system" "kyverno-reports-controller" "Kyverno reports"

  # Headlamp (namespace: devtools)
  check_ready "deployment" "devtools" "headlamp" "Headlamp"

  # OAuth2-Proxy removed — replaced by APISIX openid-connect plugin

  # SeaweedFS (namespace: storage)
  SWFS_MASTER=$(kubectl get pods -n storage -l app.kubernetes.io/component=master --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${SWFS_MASTER}" -ge 1 ]; then
    pass "SeaweedFS master: Running"
  else
    fail "SeaweedFS master: not running"
  fi

  # SeaweedFS volume
  SWFS_VOLUME=$(kubectl get pods -n storage -l app.kubernetes.io/component=volume --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${SWFS_VOLUME}" -ge 1 ]; then
    pass "SeaweedFS volume: Running"
  else
    fail "SeaweedFS volume: not running"
  fi

  # SeaweedFS filer
  SWFS_FILER=$(kubectl get pods -n storage -l app.kubernetes.io/component=filer --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${SWFS_FILER}" -ge 1 ]; then
    pass "SeaweedFS filer: Running"
  else
    fail "SeaweedFS filer: not running"
  fi

  # Harbor core (namespace: devtools)
  HARBOR_CORE=$(kubectl get pods -n devtools -l component=core --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${HARBOR_CORE}" -ge 1 ]; then
    pass "Harbor core: Running"
  else
    fail "Harbor core: not running"
  fi

  # Harbor sub-components (namespace: devtools)
  for harbor_comp in registry portal jobservice nginx; do
    HC_PODS=$(kubectl get pods -n devtools -l component="${harbor_comp}" --no-headers 2>/dev/null | grep -c "Running" || true)
    if [ "${HC_PODS}" -ge 1 ]; then
      pass "Harbor ${harbor_comp}: Running"
    else
      fail "Harbor ${harbor_comp}: not running"
    fi
  done

  # Harbor Redis (internal, namespace: devtools, label component=redis)
  HARBOR_REDIS=$(kubectl get pods -n devtools -l component=redis --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${HARBOR_REDIS}" -ge 1 ]; then
    pass "Harbor redis: Running"
  else
    fail "Harbor redis: not running"
  fi

  # OpenBao (namespace: storage — requires manual unseal, Running is expected even when sealed)
  OPENBAO=$(kubectl get pods -n storage -l app.kubernetes.io/name=openbao --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${OPENBAO}" -ge 1 ]; then
    pass "OpenBao: Running"
  else
    warn "OpenBao: not running (requires manual unseal)"
  fi

  # Velero (namespace: storage)
  check_ready "deployment" "storage" "velero" "Velero"

  # Velero node-agent DaemonSet (namespace: storage, label name=node-agent)
  VELERO_NA=$(kubectl get pods -n storage -l name=node-agent --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${VELERO_NA}" -ge "${TOPOLOGY_NODES}" ]; then
    pass "Velero node-agent: ${VELERO_NA} Running"
  elif [ "${VELERO_NA}" -ge 1 ]; then
    warn "Velero node-agent: ${VELERO_NA} Running (expected >= ${TOPOLOGY_NODES})"
  else
    fail "Velero node-agent: not running"
  fi

  # Velero BackupStorageLocation (namespace: storage)
  BSL_PHASE=$(kubectl get backupstoragelocation default -n storage -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
  if [ "${BSL_PHASE}" = "Available" ]; then
    pass "Velero BSL default: Available"
  else
    warn "Velero BSL default: ${BSL_PHASE:-not found} (BSL may be Unavailable if SeaweedFS S3 is not seeded)"
  fi

  # OpenBao encrypted snapshot backup activation (#757)
  if declare -F openbao_backup_check_activation >/dev/null 2>&1; then
    ob_rc=0
    openbao_backup_check_activation || ob_rc=$?
    case "${OPENBAO_BACKUP_STATE}" in
      ok)        pass "OpenBao snapshot backup: ${OPENBAO_BACKUP_MSG}" ;;
      degraded)  warn "OpenBao snapshot backup DEGRADED: ${OPENBAO_BACKUP_MSG} (see docs/runbooks/openbao-snapshot-backup.md)" ;;
      suspended) warn "OpenBao snapshot backup: CronJob is suspended" ;;
      absent)    skip "OpenBao snapshot backup: CronJob storage/openbao-snapshot not present" ;;
      *)         warn "OpenBao snapshot backup check inconclusive (${ob_rc}): ${OPENBAO_BACKUP_MSG}" ;;
    esac
  else
    warn "OpenBao snapshot backup helper not found (${_OPENBAO_BK_LIB})"
  fi
  echo ""
fi

#=========================================
# 13. GITOPS (Gitea, ArgoCD)
#=========================================
if should_run "gitops"; then
  echo "--- [13/17] GitOps ---"
  # Gitea (namespace: devtools)
  GITEA_PODS=$(kubectl get pods -n devtools -l app.kubernetes.io/name=gitea --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${GITEA_PODS}" -ge 1 ]; then
    pass "Gitea: Running"
  else
    fail "Gitea: not running"
  fi

  # Gitea HTTP health check via HTTPS route (headless service, can't curl ClusterIP)
  GITEA_CODE=$(curl -sk -o /dev/null -w '%{http_code}' "https://gitea.${DOMAIN}/api/v1/version" --connect-timeout 5 2>/dev/null || echo "000")
  if [ "${GITEA_CODE}" = "200" ]; then
    pass "Gitea HTTP API: responsive (HTTPS ${GITEA_CODE})"
  else
    fail "Gitea HTTP API: HTTP ${GITEA_CODE}"
  fi

  # D-505-GITEA: private repositories hide existence from anonymous callers.
  # Reuse push-to-gitea.sh's Secret; missing credentials fail before HTTP.
  GITEA_PW=$(kubectl get secret gitea-admin -n devtools -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d) || GITEA_PW=""
  if [ -z "${GITEA_PW}" ]; then
    fail "Gitea narwhal-gitops repo: admin credential unavailable"
  else
    REPO_CODE=$(curl -sk -u "gitea-admin:${GITEA_PW}" -o /dev/null -w '%{http_code}' "https://gitea.${DOMAIN}/api/v1/repos/gitea-admin/narwhal-gitops" --connect-timeout 5 2>/dev/null || echo "000")
    if [ "${REPO_CODE}" = "200" ]; then
      pass "Gitea narwhal-gitops repo: exists"
    else
      warn "Gitea narwhal-gitops repo: HTTP ${REPO_CODE} (created by 14-gitops-bootstrap.sh)"
    fi
  fi
  unset GITEA_PW

  # ArgoCD (namespace: devtools)
  check_ready "deployment" "devtools" "argocd-server" "ArgoCD server"
  check_ready "deployment" "devtools" "argocd-repo-server" "ArgoCD repo-server"

  ARGOCD_CTRL=$(kubectl get pods -n devtools -l app.kubernetes.io/name=argocd-application-controller --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${ARGOCD_CTRL}" -ge 1 ]; then
    pass "ArgoCD app-controller: Running"
  else
    fail "ArgoCD app-controller: not running"
  fi

  # ArgoCD Redis (namespace: devtools)
  ARGOCD_REDIS=$(kubectl get pods -n devtools -l app.kubernetes.io/name=argocd-redis --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${ARGOCD_REDIS}" -ge 1 ]; then
    pass "ArgoCD redis: Running"
  else
    fail "ArgoCD redis: not running"
  fi

  # App-of-Apps (namespace: devtools)
  AOA_STATUS=$(kubectl get application idp-apps -n devtools -o jsonpath='{.status.sync.status}' 2>/dev/null || echo "")
  AOA_HEALTH=$(kubectl get application idp-apps -n devtools -o jsonpath='{.status.health.status}' 2>/dev/null || echo "")
  if [ -n "${AOA_STATUS}" ]; then
    pass "App-of-Apps: sync=${AOA_STATUS}, health=${AOA_HEALTH}"
  else
    warn "App-of-Apps: not found"
  fi
  echo ""
fi

#=========================================
# 14. GATEWAY ROUTES (ApisixRoute — all in platform-system)
#=========================================
if should_run "routes"; then
  echo "--- [14/17] Gateway Routes ---"
  # HTTPRoutes are gone; cluster uses ApisixRoute CRDs (apisix.apache.org).
  # All 17 routes live in namespace platform-system.
  APISIX_ROUTES=(
    "argocd"
    "grafana"
    "gitea"
    "harbor"
    "keycloak"
    "headlamp"
    "openbao"
    "hubble"
    "prometheus"
  )

  for route_name in "${APISIX_ROUTES[@]}"; do
    ROUTE_EXISTS=$(kubectl get apisixroute "${route_name}" -n platform-system -o jsonpath='{.metadata.name}' 2>/dev/null || echo "")
    if [ -n "${ROUTE_EXISTS}" ]; then
      pass "ApisixRoute ${route_name}: exists (platform-system)"
    else
      fail "ApisixRoute ${route_name}: not found in platform-system"
    fi
  done

  # HTTPS connectivity test (only if not quick mode)
  if ! ${QUICK_MODE}; then
    echo ""
    echo "  --- HTTPS Connectivity ---"
    for host in argocd grafana gitea harbor keycloak headlamp openbao hubble prometheus; do
      CODE=$(curl -sk -o /dev/null -w '%{http_code}' "https://${host}.${DOMAIN}" 2>/dev/null || echo "000")
      case "${CODE}" in
        200|301|302|303|307|308)
          pass "https://${host}.${DOMAIN}: HTTP ${CODE}" ;;
        *)
          fail "https://${host}.${DOMAIN}: HTTP ${CODE}" ;;
      esac
    done
  fi
  echo ""
fi

#=========================================
# 15. ISTIO AMBIENT MESH
#=========================================
if should_run "istio"; then
  echo "--- [15/17] Istio Ambient Mesh ---"
  # istiod
  check_ready "deployment" "istio-system" "istiod" "istiod"

  # istio-cni DaemonSet
  ISTIO_CNI=$(kubectl get pods -n istio-system -l k8s-app=istio-cni-node --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${ISTIO_CNI}" -ge "${TOPOLOGY_NODES}" ]; then
    pass "istio-cni: ${ISTIO_CNI} Running"
  elif [ "${ISTIO_CNI}" -ge 1 ]; then
    warn "istio-cni: ${ISTIO_CNI} Running (expected >= ${TOPOLOGY_NODES})"
  else
    fail "istio-cni: not running"
  fi

  # ztunnel DaemonSet
  ZTUNNEL=$(kubectl get pods -n istio-system -l app=ztunnel --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${ZTUNNEL}" -ge "${TOPOLOGY_NODES}" ]; then
    pass "ztunnel: ${ZTUNNEL} Running"
  elif [ "${ZTUNNEL}" -ge 1 ]; then
    warn "ztunnel: ${ZTUNNEL} Running (expected >= ${TOPOLOGY_NODES})"
  else
    fail "ztunnel: not running"
  fi

  # PeerAuthentication STRICT
  PA_MODE=$(kubectl get peerauthentication default -n istio-system -o jsonpath='{.spec.mtls.mode}' 2>/dev/null || echo "")
  if [ "${PA_MODE}" = "STRICT" ]; then
    pass "PeerAuthentication: STRICT mTLS"
  else
    fail "PeerAuthentication: ${PA_MODE:-not found} (expected STRICT)"
  fi

  # D-505-AMBIENT: mirror 09-istio-ambient.sh's enrollment, pinned by R293.
  # Checking names catches a missing namespace even when unrelated ones join.
  # Cost: duplicated list; move to a shared library if more consumers appear.
  AMBIENT_NAMESPACES=(platform-system devtools monitoring storage database)
  for ns in "${AMBIENT_NAMESPACES[@]}"; do
    AMBIENT_MODE=$(kubectl get ns "${ns}" -o jsonpath='{.metadata.labels.istio\.io/dataplane-mode}' 2>/dev/null || echo "")
    if [ "${AMBIENT_MODE}" = "ambient" ]; then
      pass "Ambient namespace ${ns}: enrolled"
    else
      fail "Ambient namespace ${ns}: ${AMBIENT_MODE:-missing} (expected ambient)"
    fi
  done

  # D-DEV-OPTOUT: gitops/resources/dev-namespace.yaml opts dev out of the mesh
  # on purpose (SSO cookies); R293 ties this to the yaml.
  DEV_MODE=$(kubectl get ns dev -o jsonpath='{.metadata.labels.istio\.io/dataplane-mode}' 2>/dev/null || echo "")
  if [ "${DEV_MODE}" = "ambient" ]; then
    fail "Namespace dev: ambient (GitOps opts it out: dataplane-mode none)"
  else
    pass "Namespace dev: not ambient (${DEV_MODE:-unlabeled})"
  fi

  echo ""
fi

#=========================================
# 16. POD HEALTH
#=========================================
if should_run "podhealth"; then
  echo "--- [16/17] Pod Health ---"

  # CrashLoopBackOff pods
  CRASHLOOP_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | grep "CrashLoopBackOff" || echo "")
  if [ -z "${CRASHLOOP_PODS}" ]; then
    pass "No CrashLoopBackOff pods"
  else
    CRASHLOOP_COUNT=$(echo "${CRASHLOOP_PODS}" | wc -l | tr -d ' ')
    fail "${CRASHLOOP_COUNT} CrashLoopBackOff pods found"
  fi

  # Containers not ready (READY column mismatch like 0/1, 1/2)
  NOTREADY_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | grep "Running" | awk '{split($3, a, "/"); if (a[1] != a[2]) print $0}' || echo "")
  # Exclude OpenBao (requires manual unseal, 0/1 Running is expected)
  NOTREADY_FILTERED=$(echo "${NOTREADY_PODS}" | grep -v "openbao" || true)
  if [ -z "${NOTREADY_FILTERED}" ]; then
    if [ -n "${NOTREADY_PODS}" ]; then
      pass "All running containers ready (openbao excluded — needs unseal)"
    else
      pass "All running containers ready"
    fi
  else
    NOTREADY_COUNT=$(echo "${NOTREADY_FILTERED}" | wc -l | tr -d ' ')
    fail "${NOTREADY_COUNT} running pods with containers not ready"
  fi

  # D-576-PROBE-DENY: a Running-but-not-Ready pod whose kubelet probes ztunnel rejects lost its
  # probe exemption (ipset/redirect) at a node restart; it stays not-Ready until re-enrolled.
  # Scans NOTREADY_PODS, not NOTREADY_FILTERED: the openbao exclusion above would hide
  # openbao-agent-injector, one of the two pods #576 hit. A sealed openbao-0 has no denials, so it
  # stays quiet. Cost: one ztunnel log read per not-ready pod. Remedy: delete the pod (the
  # istio-cni re-enrolls it); see docs/common/troubleshooting.md "Probe denied by ztunnel".
  PROBE_DENY_FOUND=0
  PROBE_DENY_LOG=$(mktemp)
  while read -r pd_ns pd_pod _; do
    [ -n "${pd_pod}" ] || continue
    pd_ip=$(kubectl get pod -n "${pd_ns}" "${pd_pod}" -o jsonpath='{.status.podIP}' 2>/dev/null || true)
    pd_node=$(kubectl get pod -n "${pd_ns}" "${pd_pod}" -o jsonpath='{.spec.nodeName}' 2>/dev/null || true)
    [ -n "${pd_ip}" ] && [ -n "${pd_node}" ] || continue
    pd_host=$(kubectl get ciliumnode "${pd_node}" -o jsonpath='{.spec.addresses[?(@.type=="CiliumInternalIP")].ip}' 2>/dev/null || true)
    pd_zt=$(kubectl get pods -n istio-system -l app=ztunnel --field-selector "spec.nodeName=${pd_node}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    [ -n "${pd_host}" ] && [ -n "${pd_zt}" ] || continue
    kubectl logs -n istio-system "${pd_zt}" --since=30m > "${PROBE_DENY_LOG}" 2>/dev/null || true
    pd_n=$(ambient_probe_denials "${PROBE_DENY_LOG}" "${pd_ip}" "${pd_host}")
    if [ "${pd_n:-0}" -gt 0 ]; then
      PROBE_DENY_FOUND=$((PROBE_DENY_FOUND + 1))
      fail "${pd_ns}/${pd_pod}: ztunnel rejected ${pd_n} kubelet probe(s) in 30m (istio_converted_static_strict); fix: kubectl delete pod -n ${pd_ns} ${pd_pod}"
    fi
  done <<< "${NOTREADY_PODS}"
  rm -f "${PROBE_DENY_LOG}"
  if [ "${PROBE_DENY_FOUND}" -eq 0 ]; then
    pass "No not-ready pod has kubelet probes rejected by ztunnel"
  fi

  # Failed Helm releases
  FAILED_HELM=$(helm list -A --failed --no-headers 2>/dev/null || echo "")
  if [ -z "${FAILED_HELM}" ]; then
    pass "No failed Helm releases"
  else
    FAILED_HELM_COUNT=$(echo "${FAILED_HELM}" | wc -l | tr -d ' ')
    fail "${FAILED_HELM_COUNT} failed Helm releases found"
  fi

  # ArgoCD Applications health (if ArgoCD is running; namespace: devtools)
  ARGOCD_RUNNING=$(kubectl get pods -n devtools -l app.kubernetes.io/name=argocd-server --no-headers 2>/dev/null | grep -c "Running" || true)
  if [ "${ARGOCD_RUNNING}" -gt 0 ]; then
    # Get all applications: Healthy is required, OutOfSync is warn-only (common with script+ArgoCD dual management)
    ARGOCD_APPS=$(kubectl get applications -n devtools -o json 2>/dev/null | jq -r '.items[] | "\(.metadata.name)|\(.status.sync.status)|\(.status.health.status)"' 2>/dev/null || echo "")
    if [ -n "${ARGOCD_APPS}" ]; then
      # Health check: Degraded/Missing is FAIL
      DEGRADED_APPS=$(echo "${ARGOCD_APPS}" | awk -F'|' '{if ($3 != "Healthy" && $3 != "Progressing" && $3 != "") print $1 " (health=" $3 ")"}' || echo "")
      # Sync check: OutOfSync/Unknown is WARN only (not FAIL)
      OUTOFSYNC_APPS=$(echo "${ARGOCD_APPS}" | awk -F'|' '{if ($2 != "Synced" && $2 != "") print $1 " (sync=" $2 ")"}' || echo "")

      APP_COUNT=$(echo "${ARGOCD_APPS}" | wc -l | tr -d ' ')
      if [ -z "${DEGRADED_APPS}" ] && [ -z "${OUTOFSYNC_APPS}" ]; then
        pass "All ${APP_COUNT} ArgoCD apps Synced+Healthy"
      elif [ -z "${DEGRADED_APPS}" ]; then
        OUTOFSYNC_COUNT=$(echo "${OUTOFSYNC_APPS}" | wc -l | tr -d ' ')
        warn "${OUTOFSYNC_COUNT}/${APP_COUNT} ArgoCD apps OutOfSync (all Healthy): ${OUTOFSYNC_APPS}"
      else
        if [ -n "${DEGRADED_APPS}" ]; then
          DEGRADED_COUNT=$(echo "${DEGRADED_APPS}" | wc -l | tr -d ' ')
          fail "${DEGRADED_COUNT} ArgoCD apps not Healthy: ${DEGRADED_APPS}"
        fi
      fi
    else
      warn "ArgoCD apps: no applications found or jq not available"
    fi
  else
    warn "ArgoCD app health: skipped (ArgoCD not running)"
  fi
  echo ""
fi

#=========================================
# 17. PROBLEM PODS (global check)
#=========================================
echo "--- [17/17] Problem Pods ---"
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
echo ""

#=========================================
# SUMMARY
#=========================================
TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT + SKIP_COUNT))
echo "============================================"
echo "VERIFICATION SUMMARY"
echo "============================================"
echo "  Total checks: ${TOTAL}"
echo "  PASS: ${PASS_COUNT}"
echo "  FAIL: ${FAIL_COUNT}"
echo "  WARN: ${WARN_COUNT}"
echo "  SKIP: ${SKIP_COUNT}"
echo ""

TOTAL_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
RUNNING_PODS=$(kubectl get pods -A --no-headers 2>/dev/null | grep -c "Running" || true)
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
