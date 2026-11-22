#!/bin/bash
# lib-harbor-ca.sh — Shared Harbor containerd CA validation, late-join sync, and Phase 2 check
# (KISA-IMG-01, D-HARBOR-NODE-CA).
#
# Sourced by:
#   - scripts/cluster/08-6b-harbor-containerd-tls-node.sh
#   - scripts/cluster/02-join-worker.sh
#   - scripts/cluster/02-join-control-plane.sh
#   - scripts/cluster/06-phase2-start.sh
#   - scripts/cloud/apply-harbor-ca-kakao.sh
#   - scripts/cloud/distribute-join-kakao.sh
#
# Must remain bash 3.2 compatible (macOS behavioral tests + Linux nodes).

# Validate every PEM certificate block in a CA bundle file.
# Reuses scripts/airgap/lib/push-security-db.sh validate_ca_cert's awk-split + openssl x509 +
# -checkend 0 + python3 -startdate approach, tightened for trust-store installation:
#   - rejects missing, unreadable, or empty files
#   - rejects any non-blank text outside BEGIN/END CERTIFICATE (trailing garbage, private keys,
#     foreign PEM blocks, unclosed headers, or non-base64 content inside a block)
#   - rejects any expired (-checkend 0) or not-yet-valid (-startdate > now) certificate block
#   - never prints certificate contents to stdout or stderr
validate_harbor_ca_bundle() {
  local cert="$1"
  if [ ! -f "${cert}" ]; then
    echo "ERROR: Harbor CA file not found: ${cert}" >&2
    return 1
  fi
  if [ ! -r "${cert}" ]; then
    echo "ERROR: Harbor CA file is not readable: ${cert}" >&2
    return 1
  fi
  if [ ! -s "${cert}" ]; then
    echo "ERROR: Harbor CA file is empty: ${cert}" >&2
    return 1
  fi
  if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl is required to validate Harbor CA bundle" >&2
    return 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is required to validate Harbor CA certificate notBefore timestamps" >&2
    return 1
  fi

  local ca_tmpdir
  ca_tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/harbor-ca-val.XXXXXX")"

  if ! awk -v dir="${ca_tmpdir}" '
    { sub(/\r$/, "") }
    in_cert {
      if ($0 == "-----END CERTIFICATE-----") {
        print > out
        close(out)
        in_cert = 0
        out = ""
      } else if ($0 ~ /^[A-Za-z0-9+\/=[:space:]]+$/) {
        print > out
      } else {
        bad = 1
        exit 2
      }
      next
    }
    $0 == "-----BEGIN CERTIFICATE-----" {
      in_cert = 1
      count++
      out = sprintf("%s/cert-%04d.crt", dir, count)
      print > out
      next
    }
    /^[[:space:]]*$/ { next }
    {
      bad = 1
      exit 2
    }
    END {
      if (in_cert || bad || count == 0) {
        exit 2
      }
    }
  ' "${cert}"; then
    rm -rf "${ca_tmpdir}"
    echo "ERROR: Harbor CA input contains invalid PEM structure, foreign block (e.g. private key), or trailing garbage: ${cert}" >&2
    return 1
  fi

  local block_count=0
  local b
  for b in "${ca_tmpdir}"/cert-*.crt; do
    [ -f "${b}" ] || continue
    block_count=$((block_count + 1))

    if ! openssl x509 -in "${b}" -noout >/dev/null 2>&1; then
      rm -rf "${ca_tmpdir}"
      echo "ERROR: Harbor CA block #${block_count} is not a valid X.509 certificate: ${cert}" >&2
      return 1
    fi
    if ! openssl x509 -in "${b}" -noout -checkend 0 >/dev/null 2>&1; then
      rm -rf "${ca_tmpdir}"
      echo "ERROR: Harbor CA block #${block_count} has expired: ${cert}" >&2
      return 1
    fi

    local nb
    nb="$(openssl x509 -in "${b}" -noout -startdate 2>/dev/null | cut -d= -f2-)"
    if [ -z "${nb}" ] || ! python3 -c "
import sys, datetime
raw = sys.argv[1].strip()
cleaned = ' '.join(raw.split())
t = datetime.datetime.strptime(cleaned, '%b %d %H:%M:%S %Y GMT').replace(tzinfo=datetime.timezone.utc)
sys.exit(0 if t <= datetime.datetime.now(datetime.timezone.utc) else 1)
" "${nb}" >/dev/null 2>&1; then
      rm -rf "${ca_tmpdir}"
      echo "ERROR: Harbor CA block #${block_count} is not yet valid or has an unparseable notBefore date: ${cert}" >&2
      return 1
    fi
  done

  rm -rf "${ca_tmpdir}"
  if [ "${block_count}" -eq 0 ]; then
    echo "ERROR: Harbor CA input contains no PEM certificate blocks: ${cert}" >&2
    return 1
  fi
  return 0
}

# Probe whether a remote Harbor CA file exists and is non-empty over a caller-supplied
# transport command (e.g. sshpass ssh on Vagrant, ssh_node on Kakao).
# The remote command prints an explicit HARBOR_CA_PRESENT or HARBOR_CA_ABSENT sentinel so
# SSH/transport/sudo failures (non-zero rc or unexpected output) can never be misclassified
# as "CA not issued yet".
#
# Usage: probe_remote_harbor_ca <remote_ca_path> <use_sudo:0|1> <transport_cmd...>
# Returns:
#   0 -> HARBOR_CA_PRESENT
#   2 -> HARBOR_CA_ABSENT (not issued yet — initial Phase 1 join)
#   1 -> transport/sudo error or unexpected output (fail closed)
probe_remote_harbor_ca() {
  local remote_ca="${1:-}"
  local use_sudo="${2:-0}"
  if [ "$#" -lt 3 ]; then
    echo "ERROR: probe_remote_harbor_ca requires <remote_ca> <use_sudo> <transport_cmd...>" >&2
    return 1
  fi
  shift 2
  case "${remote_ca}" in
    ""|*[!A-Za-z0-9_/.-]*)
      echo "ERROR: invalid remote Harbor CA path: ${remote_ca}" >&2
      return 1
      ;;
  esac

  local remote_cmd
  if [ "${use_sudo}" = "1" ]; then
    # Run the test inside `sudo sh -c` so a sudo failure itself exits non-zero instead of
    # falling into the else branch and falsely printing HARBOR_CA_ABSENT.
    remote_cmd="sudo sh -c 'if [ -s \"\$1\" ]; then printf \"HARBOR_CA_PRESENT\\n\"; else printf \"HARBOR_CA_ABSENT\\n\"; fi' _ '${remote_ca}'"
  else
    remote_cmd="if [ -s '${remote_ca}' ]; then printf 'HARBOR_CA_PRESENT\n'; else printf 'HARBOR_CA_ABSENT\n'; fi"
  fi

  local probe_out=""
  local probe_rc=0
  probe_out="$("$@" "${remote_cmd}" 2>&1)" || probe_rc=$?
  if [ "${probe_rc}" -ne 0 ]; then
    echo "ERROR: remote probe failed while checking Harbor CA (${remote_ca}, rc=${probe_rc}): ${probe_out}" >&2
    return 1
  fi

  local has_present=0
  local has_absent=0
  case $'\n'"${probe_out}"$'\n' in
    *$'\n'HARBOR_CA_PRESENT$'\n'*) has_present=1 ;;
  esac
  case $'\n'"${probe_out}"$'\n' in
    *$'\n'HARBOR_CA_ABSENT$'\n'*) has_absent=1 ;;
  esac

  if [ "${has_present}" -eq 1 ] && [ "${has_absent}" -eq 0 ]; then
    return 0
  elif [ "${has_absent}" -eq 1 ] && [ "${has_present}" -eq 0 ]; then
    return 2
  fi
  echo "ERROR: unexpected Harbor CA probe output (${remote_ca}): ${probe_out}" >&2
  return 1
}

# Synchronize Harbor containerd CA trust for late-joining control-plane or worker nodes
# (nodes added after Phase 2 08-6-tls-routes.sh already ran).
# During initial Phase 1 bring-up, master-1 does not have the CA yet and explicitly reports
# HARBOR_CA_ABSENT (clean skip). Any SSH/transport failure fails closed (exit non-zero) so a
# late-joining node can never silently proceed without Harbor TLS trust on a transient SSH error.
#
# D-HARBOR-NODE-CA transport trust (D5):
#   On Vagrant, node-to-node fetch uses the same sshpass channel that transports
#   join-command.sh / join-control-plane.sh / encryption-config.yaml / oidc-ca.crt
#   (see D-JOIN-OIDC-CA-HASH in 02-join-control-plane.sh). No universally node-readable
#   Kubernetes copy of the Narwhal Root CA exists for an unscheduled/joining node:
#   kube-public/{cluster-info,kube-root-ca.crt} carry only the Kubernetes API server CA and are
#   blocked to system:nodes by NodeAuthorizer; platform-system/narwhal-wildcard-tls is blocked
#   by NodeAuthorizer; and devtools/narwhal-ca-cert is readable via kubelet.conf only when a pod
#   mounting it is already scheduled on that node. Residual risk: an on-path attacker on the
#   Vagrant host-only lab network could intercept this channel (Kakao Cloud pins host keys via
#   scripts/cloud/lib-ssh.sh).
sync_harbor_containerd_ca() {
  local master_ip="${1:-}"
  local domain="${2:-${DOMAIN:-local.narwhal.internal}}"
  if [ -z "${master_ip}" ] && [ "${PROVIDER:-vagrant}" != "kakao" ]; then
    echo "ERROR: sync_harbor_containerd_ca requires master IP" >&2
    return 1
  fi

  local node_script="${STAGE_DIR:-/home/vagrant}/scripts/cluster/08-6b-harbor-containerd-tls-node.sh"
  if [ ! -f "${node_script}" ]; then
    node_script="$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" 2>/dev/null && pwd || true)/08-6b-harbor-containerd-tls-node.sh"
  fi

  local harbor_ca_remote="/etc/containerd/certs.d/harbor.${domain}/ca.crt"
  local tmp_ca=""
  local cleanup_tmp_ca=0

  if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
    if [ -f /tmp/harbor-ca.crt ]; then
      tmp_ca="/tmp/harbor-ca.crt"
    else
      echo "PROVIDER=kakao: /tmp/harbor-ca.crt not staged (initial Phase 1 join; Phase 2 apply-harbor-ca-kakao.sh will configure Harbor CA)"
      return 0
    fi
  else
    local probe_rc=0
    probe_remote_harbor_ca "${harbor_ca_remote}" 0 \
      sshpass -p "vagrant" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "vagrant@${master_ip}" || probe_rc=$?
    if [ "${probe_rc}" -eq 2 ]; then
      echo "Harbor CA not yet provisioned on master-1 (${harbor_ca_remote}); 08-6-tls-routes.sh will distribute it in Phase 2"
      return 0
    elif [ "${probe_rc}" -ne 0 ]; then
      return 1
    fi

    tmp_ca="$(mktemp "${TMPDIR:-/tmp}/harbor-ca.XXXXXX")"
    cleanup_tmp_ca=1
    if ! sshpass -p "vagrant" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "vagrant@${master_ip}:${harbor_ca_remote}" "${tmp_ca}" 2>/dev/null; then
      rm -f "${tmp_ca}"
      echo "ERROR: Harbor CA exists on master-1 (${harbor_ca_remote}) but could not be copied to this node" >&2
      return 1
    fi
  fi

  if [ ! -f "${node_script}" ]; then
    [ "${cleanup_tmp_ca}" -eq 1 ] && rm -f "${tmp_ca}"
    echo "ERROR: ${node_script} missing; cannot install Harbor containerd CA" >&2
    return 1
  fi

  local rc=0
  sudo env DOMAIN="${domain}" STAGE_DIR="${STAGE_DIR:-/home/vagrant}" bash "${node_script}" "${tmp_ca}" "${domain}" || rc=$?
  [ "${cleanup_tmp_ca}" -eq 1 ] && rm -f "${tmp_ca}"
  return "${rc}"
}

# Verify local node's containerd Harbor TLS trust files (used by 06-phase2-start.sh).
# Sets HARBOR_CA_LOCAL_STATE (ok|degraded) and HARBOR_CA_LOCAL_MSG.
# shellcheck disable=SC2034
harbor_ca_check_local() {
  local domain="${1:-${DOMAIN:-local.narwhal.internal}}"
  local certs_dir="${2:-${CERTS_DIR:-/etc/containerd/certs.d}}"
  local harbor_dir="${certs_dir}/harbor.${domain}"
  local ca_file="${harbor_dir}/ca.crt"
  local hosts_file="${harbor_dir}/hosts.toml"

  if [ ! -f "${ca_file}" ]; then
    HARBOR_CA_LOCAL_STATE="degraded"
    HARBOR_CA_LOCAL_MSG="Harbor CA file missing at ${ca_file}"
    return 1
  fi
  if ! validate_harbor_ca_bundle "${ca_file}" >/dev/null 2>&1; then
    HARBOR_CA_LOCAL_STATE="degraded"
    HARBOR_CA_LOCAL_MSG="Harbor CA file at ${ca_file} failed PEM/validity validation"
    return 1
  fi
  if [ ! -f "${hosts_file}" ]; then
    HARBOR_CA_LOCAL_STATE="degraded"
    HARBOR_CA_LOCAL_MSG="containerd hosts.toml missing at ${hosts_file}"
    return 1
  fi
  if grep -Eq '^[[:space:]]*skip_verify[[:space:]]*=' "${hosts_file}"; then
    HARBOR_CA_LOCAL_STATE="degraded"
    HARBOR_CA_LOCAL_MSG="${hosts_file} still contains skip_verify"
    return 1
  fi
  if ! grep -Fq "ca = \"${ca_file}\"" "${hosts_file}"; then
    HARBOR_CA_LOCAL_STATE="degraded"
    HARBOR_CA_LOCAL_MSG="${hosts_file} does not pin ca = \"${ca_file}\""
    return 1
  fi

  HARBOR_CA_LOCAL_STATE="ok"
  HARBOR_CA_LOCAL_MSG="containerd Harbor TLS verified at ${hosts_file}"
  return 0
}
