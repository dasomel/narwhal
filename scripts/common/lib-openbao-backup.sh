#!/usr/bin/env bash
# Shared activation check for the encrypted OpenBao raft snapshot backup (#757).
#
# D-OPENBAO-BACKUP-ACTIVATION: keep applying ConfigMap storage/openbao-backup-recipient a
# documented manual operator step rather than auto-applying a cert file from synced/staged
# repo directories during Phase 2. Reason: Vagrant synced_folder and stage-kakao-nodes.sh
# push configs/ to cluster VMs, so an operator mistaking *.key.pem for *.cert.pem would
# copy the private key onto master-1 before Phase 2 could reject it (weakening D-cms), and
# activation is per-cluster (D-fpr). Cost: every rebuild requires re-applying the public
# certificate (or running keygen for a new cluster). Escape hatch: Phase 2, verify-cluster,
# verify-backup, show-credentials, and Prometheus alerts surface missing/mismatched state
# loudly so the gap cannot stay silent.
#
# Safe under `set -euo pipefail`, `set -u`, and macOS bash 3.2. Never prints secret material.
# Output variables below are consumed by sourcing callers (`06-phase2-start.sh`, `verify-*.sh`, `show-credentials.sh`).
# shellcheck disable=SC2034

# Checks whether CronJob storage/openbao-snapshot is declared & active and whether
# ConfigMap storage/openbao-backup-recipient matches the pinned RECIPIENT_FPR.
# Sets:
#   OPENBAO_BACKUP_STATE         ok | degraded | absent | suspended | unknown
#   OPENBAO_BACKUP_MSG           single-line human-readable status (no secrets)
#   OPENBAO_BACKUP_LAST_SUCCESS  CronJob status.lastSuccessfulTime (RFC3339 or empty)
#   OPENBAO_BACKUP_LAST_SCHEDULE CronJob status.lastScheduleTime (RFC3339 or empty)
# Returns:
#   0 = ok, absent, or suspended
#   1 = degraded (CronJob active, ConfigMap missing/invalid/expired/private-key/mismatch/unpinned)
#   2 = query or local tool error
openbao_backup_check_activation() {
  OPENBAO_BACKUP_STATE="unknown"
  OPENBAO_BACKUP_MSG=""
  OPENBAO_BACKUP_LAST_SUCCESS=""
  OPENBAO_BACKUP_LAST_SCHEDULE=""

  local cj_json="" cm_json="" cj_meta="" cert_pem=""
  local suspend="" pinned_fpr="" rc=0

  cj_json=$(kubectl get cronjob openbao-snapshot -n storage --ignore-not-found -o json 2>/dev/null) || rc=$?
  if [ "${rc}" -ne 0 ]; then
    OPENBAO_BACKUP_MSG="unable to query CronJob storage/openbao-snapshot"
    return 2
  fi
  if [ -z "${cj_json}" ]; then
    OPENBAO_BACKUP_STATE="absent"
    OPENBAO_BACKUP_MSG="CronJob storage/openbao-snapshot is not present"
    return 0
  fi

  rc=0
  cj_meta=$(printf '%s' "${cj_json}" | python3 -c '
import json, sys
try:
    cj = json.load(sys.stdin)
except Exception:
    sys.exit(2)
if not isinstance(cj, dict) or cj.get("kind") != "CronJob":
    sys.exit(2)
spec = cj.get("spec") or {}
suspend = "true" if spec.get("suspend") is True else "false"
fpr = ""
pod = (spec.get("jobTemplate") or {}).get("spec", {}).get("template", {}).get("spec", {})
for c in pod.get("containers") or []:
    for e in c.get("env") or []:
        if e.get("name") == "RECIPIENT_FPR":
            fpr = (e.get("value") or "").strip()
status = cj.get("status") or {}
ls = status.get("lastSuccessfulTime", "") or ""
lsc = status.get("lastScheduleTime", "") or ""
print(f"{suspend}|{fpr}|{ls}|{lsc}")
' 2>/dev/null) || rc=$?
  if [ "${rc}" -ne 0 ] || [ -z "${cj_meta}" ]; then
    OPENBAO_BACKUP_MSG="unable to parse CronJob storage/openbao-snapshot metadata"
    return 2
  fi

  IFS='|' read -r suspend pinned_fpr OPENBAO_BACKUP_LAST_SUCCESS OPENBAO_BACKUP_LAST_SCHEDULE <<EOF
${cj_meta}
EOF

  if [ "${suspend}" = "true" ]; then
    OPENBAO_BACKUP_STATE="suspended"
    OPENBAO_BACKUP_MSG="CronJob storage/openbao-snapshot is suspended"
    return 0
  fi

  rc=0
  cm_json=$(kubectl get configmap openbao-backup-recipient -n storage --ignore-not-found -o json 2>/dev/null) || rc=$?
  if [ "${rc}" -ne 0 ]; then
    OPENBAO_BACKUP_MSG="unable to query ConfigMap storage/openbao-backup-recipient"
    return 2
  fi
  if [ -z "${cm_json}" ]; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient is missing (openbao-snapshot pods cannot start)"
    return 1
  fi

  rc=0
  cert_pem=$(printf '%s' "${cm_json}" | python3 -c '
import json, sys
try:
    cm = json.load(sys.stdin)
except Exception:
    sys.exit(2)
val = (cm.get("data") or {}).get("recipient.pem", "")
if not isinstance(val, str):
    val = ""
sys.stdout.write(val)
' 2>/dev/null) || rc=$?
  if [ "${rc}" -ne 0 ]; then
    OPENBAO_BACKUP_MSG="unable to parse ConfigMap storage/openbao-backup-recipient"
    return 2
  fi
  if [ -z "${cert_pem}" ]; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient key recipient.pem is missing or empty"
    return 1
  fi

  local has_priv="" begin_cnt="0" has_cert=""
  has_priv=$(printf '%s\n' "${cert_pem}" | grep -F 'PRIVATE KEY' || true)
  if [ -n "${has_priv}" ]; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient contains PRIVATE KEY material — replace and rotate immediately"
    return 1
  fi

  begin_cnt=$(printf '%s\n' "${cert_pem}" | grep -c -- '-----BEGIN' || true)
  has_cert=$(printf '%s\n' "${cert_pem}" | grep -F -- '-----BEGIN CERTIFICATE-----' || true)
  if [ "${begin_cnt}" != "1" ] || [ -z "${has_cert}" ]; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient recipient.pem is not a single PEM certificate"
    return 1
  fi

  if ! command -v openssl >/dev/null 2>&1; then
    OPENBAO_BACKUP_MSG="openssl not found; cannot validate recipient certificate fingerprint"
    return 2
  fi

  if ! printf '%s\n' "${cert_pem}" | openssl x509 -noout >/dev/null 2>&1; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient recipient.pem is malformed"
    return 1
  fi

  if ! printf '%s\n' "${cert_pem}" | openssl x509 -noout -checkend 86400 >/dev/null 2>&1; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient certificate is expired or expires within 24h"
    return 1
  fi

  local fpr_line="" actual_colon="" actual_norm="" want_norm=""
  fpr_line=$(printf '%s\n' "${cert_pem}" | openssl x509 -noout -fingerprint -sha256 2>/dev/null || true)
  actual_colon=$(printf '%s' "${fpr_line}" | sed 's/^.*=//' | tr -d ' \r\n' | tr '[:lower:]' '[:upper:]')
  actual_norm=$(printf '%s' "${actual_colon}" | tr -d ':')
  if [ -z "${actual_norm}" ]; then
    OPENBAO_BACKUP_MSG="unable to compute recipient certificate SHA-256 fingerprint"
    return 2
  fi

  want_norm=$(printf '%s' "${pinned_fpr}" | tr -d ': \r\n' | tr '[:lower:]' '[:upper:]')
  if [ -z "${want_norm}" ]; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="CronJob storage/openbao-snapshot RECIPIENT_FPR is empty (recipient ${actual_colon} is not pinned)"
    return 1
  fi

  if [ "${actual_norm}" != "${want_norm}" ]; then
    OPENBAO_BACKUP_STATE="degraded"
    OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient fingerprint ${actual_colon} differs from CronJob RECIPIENT_FPR pin ${pinned_fpr}"
    return 1
  fi

  OPENBAO_BACKUP_STATE="ok"
  OPENBAO_BACKUP_MSG="ConfigMap storage/openbao-backup-recipient matches pinned RECIPIENT_FPR (${actual_colon})"
  return 0
}
