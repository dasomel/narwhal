#!/usr/bin/env bash
set -uo pipefail

#=========================================
# Backup Verification Suite
#=========================================
# Validates the full backup stack including:
# - Velero server, BSL, schedules, and recent backup status
# - SeaweedFS S3 endpoint and velero bucket availability
# - CNPG narwhal-db cluster health and WAL archiving
# - (Optional) Restore simulation with --restore flag
#
# Usage:
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-backup.sh"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-backup.sh --restore"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-backup.sh --section=velero"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-backup.sh --section=seaweedfs"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-backup.sh --section=cnpg"

SECTION_FILTER=""
RUN_RESTORE=false

for arg in "$@"; do
  case "${arg}" in
    --section=*) SECTION_FILTER="${arg#--section=}" ;;
    --restore)   RUN_RESTORE=true ;;
  esac
done

if [ -f /home/vagrant/.kube/config-local ]; then
  export KUBECONFIG=/home/vagrant/.kube/config-local
fi

SEAWEEDFS_S3_URL="http://seaweedfs-s3.storage.svc.cluster.local:8333"
VELERO_NS="storage"
DATABASE_NS="database"
RESTORE_TEST_NS="restore-test"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  [PASS] $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  [FAIL] $1"; }
warn() { WARN_COUNT=$((WARN_COUNT + 1)); echo "  [WARN] $1"; }

should_run() {
  [ -z "${SECTION_FILTER}" ] || [ "${SECTION_FILTER}" = "$1" ]
}

# kubectl wrapper — returns empty string on failure instead of exiting
kube_get() {
  kubectl "$@" 2>/dev/null || true
}

echo "============================================"
echo "Backup Verification Suite"
echo "============================================"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

#=========================================
# 1. VELERO STATUS
#=========================================
if should_run "velero"; then
  echo "[1/4] Velero Status"

  # Velero server pod running
  VELERO_PODS=$(kube_get get pods -n "${VELERO_NS}" -l app.kubernetes.io/name=velero \
    --no-headers | { grep -c "Running" || true; })
  if [ "${VELERO_PODS}" -ge 1 ]; then
    pass "Velero server pod is Running (${VELERO_PODS} pod(s))"
  else
    fail "Velero server pod is not Running"
  fi

  # Node Agent DaemonSet ready
  NODE_AGENT_DESIRED=$(kube_get get daemonset node-agent -n "${VELERO_NS}" \
    -o jsonpath='{.status.desiredNumberScheduled}')
  NODE_AGENT_READY=$(kube_get get daemonset node-agent -n "${VELERO_NS}" \
    -o jsonpath='{.status.numberReady}')
  if [ -n "${NODE_AGENT_DESIRED}" ] && [ "${NODE_AGENT_READY}" = "${NODE_AGENT_DESIRED}" ] && [ "${NODE_AGENT_DESIRED}" -gt 0 ]; then
    pass "Velero node-agent DaemonSet ready (${NODE_AGENT_READY}/${NODE_AGENT_DESIRED})"
  else
    warn "Velero node-agent DaemonSet not fully ready (${NODE_AGENT_READY:-0}/${NODE_AGENT_DESIRED:-?})"
  fi

  # BackupStorageLocation Available
  BSL_PHASE=$(kube_get get backupstoragelocation default -n "${VELERO_NS}" \
    -o jsonpath='{.status.phase}')
  if [ "${BSL_PHASE}" = "Available" ]; then
    pass "BackupStorageLocation 'default' is Available"
  else
    fail "BackupStorageLocation 'default' phase: '${BSL_PHASE:-unknown}' (expected Available)"
  fi

  # Schedule count
  SCHEDULE_COUNT=$(kube_get get schedule -n "${VELERO_NS}" --no-headers | wc -l | tr -d ' ')
  if [ "${SCHEDULE_COUNT}" -ge 1 ]; then
    pass "Velero schedules exist (${SCHEDULE_COUNT} schedule(s))"
    kube_get get schedule -n "${VELERO_NS}" --no-headers | awk '{printf "         - %s (%s)\n", $1, $4}' || true
  else
    fail "No Velero schedules found (expected >= 1)"
  fi

  # Most recent backup status
  LATEST_BACKUP=$(kube_get get backup -n "${VELERO_NS}" \
    --sort-by='.metadata.creationTimestamp' \
    -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || true)

  if [ -n "${LATEST_BACKUP}" ]; then
    BACKUP_PHASE=$(kube_get get backup "${LATEST_BACKUP}" -n "${VELERO_NS}" \
      -o jsonpath='{.status.phase}')
    BACKUP_START=$(kube_get get backup "${LATEST_BACKUP}" -n "${VELERO_NS}" \
      -o jsonpath='{.status.startTimestamp}')

    if [ "${BACKUP_PHASE}" = "Completed" ]; then
      pass "Latest backup '${LATEST_BACKUP}' is Completed"
    elif [ "${BACKUP_PHASE}" = "InProgress" ]; then
      warn "Latest backup '${LATEST_BACKUP}' is InProgress (started: ${BACKUP_START})"
    else
      fail "Latest backup '${LATEST_BACKUP}' phase: '${BACKUP_PHASE:-unknown}' (started: ${BACKUP_START})"
    fi

    # Backup age check (within 24h = 86400 seconds)
    if [ -n "${BACKUP_START}" ]; then
      BACKUP_TS=$(date -d "${BACKUP_START}" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%SZ" "${BACKUP_START}" +%s 2>/dev/null || echo "0")
      NOW_TS=$(date +%s)
      AGE_SECONDS=$(( NOW_TS - BACKUP_TS ))
      AGE_HOURS=$(( AGE_SECONDS / 3600 ))
      if [ "${BACKUP_TS}" -gt 0 ] && [ "${AGE_SECONDS}" -le 86400 ]; then
        pass "Latest backup age: ${AGE_HOURS}h (within 24h SLO)"
      elif [ "${BACKUP_TS}" -gt 0 ]; then
        warn "Latest backup age: ${AGE_HOURS}h (exceeds 24h SLO — check schedule execution)"
      fi
    fi
  else
    warn "No backups found yet (schedules may not have triggered)"
  fi

  echo ""
fi

#=========================================
# 2. SEAWEEDFS S3 ACCESS
#=========================================
if should_run "seaweedfs"; then
  echo "[2/4] SeaweedFS S3 Access"

  # SeaweedFS filer pod running
  FILER_PODS=$(kube_get get pods -n storage -l app.kubernetes.io/component=filer \
    --no-headers | { grep -c "Running" || true; })
  if [ "${FILER_PODS}" -ge 1 ]; then
    pass "SeaweedFS filer pod is Running"
  else
    fail "SeaweedFS filer pod is not Running"
  fi

  # SeaweedFS master pod running
  MASTER_PODS=$(kube_get get pods -n storage -l app.kubernetes.io/component=master \
    --no-headers | { grep -c "Running" || true; })
  if [ "${MASTER_PODS}" -ge 1 ]; then
    pass "SeaweedFS master pod is Running"
  else
    fail "SeaweedFS master pod is not Running"
  fi

  # SeaweedFS volume pod running
  VOLUME_PODS=$(kube_get get pods -n storage -l app.kubernetes.io/component=volume \
    --no-headers | { grep -c "Running" || true; })
  if [ "${VOLUME_PODS}" -ge 1 ]; then
    pass "SeaweedFS volume pod is Running"
  else
    fail "SeaweedFS volume pod is not Running"
  fi

  # S3 endpoint reachable via a pod in the cluster
  # Use velero pod to test internal S3 endpoint accessibility
  VELERO_POD=$(kube_get get pod -n "${VELERO_NS}" -l app.kubernetes.io/name=velero \
    -o jsonpath='{.items[0].metadata.name}')

  if [ -n "${VELERO_POD}" ]; then
    S3_HTTP_CODE=$(kubectl exec -n "${VELERO_NS}" "${VELERO_POD}" \
      -- wget -qO- --server-response --timeout=10 \
      "${SEAWEEDFS_S3_URL}" 2>&1 | { grep "HTTP/" | awk '{print $2}' | tail -1 || true; })

    # SeaweedFS S3 returns 403 (NoCredentials) or 200 on root — both mean reachable
    if [ "${S3_HTTP_CODE}" = "200" ] || [ "${S3_HTTP_CODE}" = "403" ] || [ "${S3_HTTP_CODE}" = "400" ]; then
      pass "SeaweedFS S3 endpoint reachable: HTTP ${S3_HTTP_CODE}"
    else
      fail "SeaweedFS S3 endpoint unreachable: HTTP ${S3_HTTP_CODE:-no response}"
    fi

    # Check velero bucket via BackupStorageLocation lastValidationTime
    BSL_LAST_VALIDATED=$(kube_get get backupstoragelocation default -n "${VELERO_NS}" \
      -o jsonpath='{.status.lastValidationTime}')
    if [ -n "${BSL_LAST_VALIDATED}" ]; then
      pass "SeaweedFS velero bucket validated at: ${BSL_LAST_VALIDATED}"
    else
      warn "BackupStorageLocation has not been validated yet"
    fi
  else
    warn "Velero pod not found, skipping S3 endpoint check"
  fi

  echo ""
fi

#=========================================
# 3. CNPG BACKUP
#=========================================
if should_run "cnpg"; then
  echo "[3/4] CNPG Backup"

  # narwhal-db Cluster Ready condition
  CLUSTER_READY=$(kube_get get cluster narwhal-db -n "${DATABASE_NS}" \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
  if [ "${CLUSTER_READY}" = "True" ]; then
    pass "CNPG narwhal-db Cluster is Ready"
  else
    CLUSTER_MSG=$(kube_get get cluster narwhal-db -n "${DATABASE_NS}" \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}')
    fail "CNPG narwhal-db Cluster not Ready: '${CLUSTER_MSG:-unknown}'"
  fi

  # Primary pod running
  PRIMARY_POD=$(kube_get get cluster narwhal-db -n "${DATABASE_NS}" \
    -o jsonpath='{.status.currentPrimary}')
  if [ -n "${PRIMARY_POD}" ]; then
    PRIMARY_PHASE=$(kube_get get pod "${PRIMARY_POD}" -n "${DATABASE_NS}" \
      -o jsonpath='{.status.phase}')
    if [ "${PRIMARY_PHASE}" = "Running" ]; then
      pass "CNPG primary pod '${PRIMARY_POD}' is Running"
    else
      fail "CNPG primary pod '${PRIMARY_POD}' phase: '${PRIMARY_PHASE:-unknown}'"
    fi
  else
    fail "CNPG primary pod not found (cluster may not be Ready)"
  fi

  # Instance count check
  INSTANCES_READY=$(kube_get get cluster narwhal-db -n "${DATABASE_NS}" \
    -o jsonpath='{.status.readyInstances}')
  INSTANCES_TOTAL=$(kube_get get cluster narwhal-db -n "${DATABASE_NS}" \
    -o jsonpath='{.spec.instances}')
  if [ "${INSTANCES_READY}" = "${INSTANCES_TOTAL}" ] && [ -n "${INSTANCES_READY}" ]; then
    pass "CNPG instances ready: ${INSTANCES_READY}/${INSTANCES_TOTAL}"
  else
    warn "CNPG instances: ${INSTANCES_READY:-0}/${INSTANCES_TOTAL:-?} ready"
  fi

  # WAL archiving configuration check
  # The 07-cnpg.sh does NOT configure barmanObjectStore, so we check and warn accordingly
  BARMAN_CONFIG=$(kube_get get cluster narwhal-db -n "${DATABASE_NS}" \
    -o jsonpath='{.spec.backup.barmanObjectStore.destinationPath}')
  if [ -n "${BARMAN_CONFIG}" ]; then
    pass "CNPG WAL archiving configured: destinationPath=${BARMAN_CONFIG}"

    # WAL archiving status from cluster status
    WAL_ARCHIVE_ERROR=$(kube_get get cluster narwhal-db -n "${DATABASE_NS}" \
      -o jsonpath='{.status.currentPrimaryFailingSinceTimestamp}')
    if [ -z "${WAL_ARCHIVE_ERROR}" ]; then
      pass "CNPG WAL archiving: no current failure detected"
    else
      warn "CNPG cluster has been failing since: ${WAL_ARCHIVE_ERROR}"
    fi

    # Check recent ScheduledBackup CRs
    SCHEDULED_BACKUP_COUNT=$(kube_get get scheduledbackup -n "${DATABASE_NS}" \
      --no-headers | wc -l | tr -d ' ')
    if [ "${SCHEDULED_BACKUP_COUNT}" -ge 1 ]; then
      pass "CNPG ScheduledBackup resources found: ${SCHEDULED_BACKUP_COUNT}"
    else
      warn "No CNPG ScheduledBackup resources found in namespace ${DATABASE_NS}"
    fi

    # Check most recent CNPG Backup CR
    LATEST_CNPG_BACKUP=$(kube_get get backup -n "${DATABASE_NS}" \
      --sort-by='.metadata.creationTimestamp' \
      -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || true)
    if [ -n "${LATEST_CNPG_BACKUP}" ]; then
      CNPG_BACKUP_STATUS=$(kube_get get backup "${LATEST_CNPG_BACKUP}" -n "${DATABASE_NS}" \
        -o jsonpath='{.status.phase}')
      if [ "${CNPG_BACKUP_STATUS}" = "completed" ]; then
        pass "CNPG latest Backup '${LATEST_CNPG_BACKUP}' is completed"
      else
        warn "CNPG latest Backup '${LATEST_CNPG_BACKUP}' status: '${CNPG_BACKUP_STATUS:-unknown}'"
      fi
    else
      warn "No CNPG Backup CRs found in namespace ${DATABASE_NS}"
    fi
  else
    warn "CNPG WAL archiving NOT configured (no barmanObjectStore in spec.backup)"
    warn "To enable WAL archiving, add spec.backup.barmanObjectStore with SeaweedFS S3 config"
  fi

  # PgBouncer pooler status
  POOLER_READY=$(kube_get get pooler narwhal-db-pooler-rw -n "${DATABASE_NS}" \
    -o jsonpath='{.status.instances}')
  POOLER_DESIRED=$(kube_get get pooler narwhal-db-pooler-rw -n "${DATABASE_NS}" \
    -o jsonpath='{.spec.instances}')
  if [ -n "${POOLER_READY}" ] && [ "${POOLER_READY}" = "${POOLER_DESIRED}" ]; then
    pass "PgBouncer pooler instances ready: ${POOLER_READY}/${POOLER_DESIRED}"
  else
    warn "PgBouncer pooler: ${POOLER_READY:-0}/${POOLER_DESIRED:-?} ready"
  fi

  echo ""
fi

#=========================================
# 4. RESTORE SIMULATION (--restore flag only)
#=========================================
if should_run "restore" || [ "${RUN_RESTORE}" = "true" ]; then
  echo "[4/4] Restore Simulation"

  if [ "${RUN_RESTORE}" != "true" ] && [ "${SECTION_FILTER}" != "restore" ]; then
    warn "Restore simulation skipped. Use --restore or --section=restore to enable."
    echo ""
  else
    # Find the most recent Completed backup
    LATEST_COMPLETED=$(kube_get get backup -n "${VELERO_NS}" \
      --sort-by='.metadata.creationTimestamp' \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' 2>/dev/null \
      | { grep " Completed" | tail -1 | awk '{print $1}' || true; })

    if [ -z "${LATEST_COMPLETED}" ]; then
      fail "No Completed backup found to restore from"
      warn "Run a manual backup first: velero backup create manual-test --include-namespaces devtools -n ${VELERO_NS}"
    else
      pass "Using backup for restore: ${LATEST_COMPLETED}"

      # Clean up any previous restore-test namespace
      if kube_get get namespace "${RESTORE_TEST_NS}" &>/dev/null; then
        echo "  Cleaning up existing ${RESTORE_TEST_NS} namespace..."
        kubectl delete namespace "${RESTORE_TEST_NS}" --timeout=120s 2>/dev/null || true
      fi

      RESTORE_NAME="verify-restore-$(date +%Y%m%d-%H%M%S)"

      # Create restore
      echo "  Creating restore '${RESTORE_NAME}' from backup '${LATEST_COMPLETED}'..."
      if kubectl create -f - <<EOF 2>/dev/null
apiVersion: velero.io/v1
kind: Restore
metadata:
  name: ${RESTORE_NAME}
  namespace: ${VELERO_NS}
spec:
  backupName: ${LATEST_COMPLETED}
  includedNamespaces:
    - devtools
  namespaceMappings:
    devtools: ${RESTORE_TEST_NS}
  restorePVs: false
  includeClusterResources: false
EOF
      then
        pass "Restore CR '${RESTORE_NAME}' created"
      else
        fail "Failed to create Restore CR — check Velero server logs"
        echo ""
        # Skip remaining restore checks
        RESTORE_SKIP=true
      fi

      if [ "${RESTORE_SKIP:-false}" != "true" ]; then
        # Wait for restore to complete (max 5 minutes)
        echo "  Waiting for restore to complete (timeout: 5m)..."
        RESTORE_DONE=false
        for i in $(seq 1 30); do
          RESTORE_PHASE=$(kube_get get restore "${RESTORE_NAME}" -n "${VELERO_NS}" \
            -o jsonpath='{.status.phase}')
          if [ "${RESTORE_PHASE}" = "Completed" ]; then
            RESTORE_DONE=true
            break
          elif [ "${RESTORE_PHASE}" = "Failed" ] || [ "${RESTORE_PHASE}" = "PartiallyFailed" ]; then
            break
          fi
          echo "    Restore phase: ${RESTORE_PHASE:-Pending} (${i}/30)..."
          sleep 10
        done

        if [ "${RESTORE_DONE}" = "true" ]; then
          pass "Restore '${RESTORE_NAME}' completed successfully"

          # Check restored resources in restore-test namespace
          RESTORED_PODS=$(kube_get get pods -n "${RESTORE_TEST_NS}" --no-headers \
            | wc -l | tr -d ' ')
          if [ "${RESTORED_PODS}" -gt 0 ]; then
            pass "Restored resources found in namespace '${RESTORE_TEST_NS}': ${RESTORED_PODS} pod(s)"
          else
            warn "No pods found in '${RESTORE_TEST_NS}' (may be expected if backup had no running pods)"
          fi

          # Count restored resources
          RESTORED_SERVICES=$(kube_get get svc -n "${RESTORE_TEST_NS}" --no-headers \
            | wc -l | tr -d ' ')
          RESTORED_DEPLOYS=$(kube_get get deployments -n "${RESTORE_TEST_NS}" --no-headers \
            | wc -l | tr -d ' ')
          pass "Restore stats: ${RESTORED_DEPLOYS} deployment(s), ${RESTORED_SERVICES} service(s) in '${RESTORE_TEST_NS}'"
        else
          RESTORE_WARNINGS=$(kube_get get restore "${RESTORE_NAME}" -n "${VELERO_NS}" \
            -o jsonpath='{.status.warnings}')
          RESTORE_ERRORS=$(kube_get get restore "${RESTORE_NAME}" -n "${VELERO_NS}" \
            -o jsonpath='{.status.errors}')
          fail "Restore '${RESTORE_NAME}' phase: '${RESTORE_PHASE:-Timeout}' (warnings: ${RESTORE_WARNINGS:-0}, errors: ${RESTORE_ERRORS:-0})"
        fi

        # Cleanup: remove restore-test namespace
        echo "  Cleaning up '${RESTORE_TEST_NS}' namespace..."
        if kubectl delete namespace "${RESTORE_TEST_NS}" --timeout=120s 2>/dev/null; then
          pass "Cleanup: namespace '${RESTORE_TEST_NS}' deleted"
        else
          warn "Cleanup: failed to delete '${RESTORE_TEST_NS}' (clean up manually)"
        fi

        # Keep the Restore CR for audit trail (do not delete)
        pass "Restore CR '${RESTORE_NAME}' preserved for audit (namespace: ${VELERO_NS})"
      fi
    fi

    echo ""
  fi
fi

#=========================================
# SUMMARY
#=========================================
TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT))
echo "============================================"
echo "BACKUP VERIFICATION SUMMARY"
echo "============================================"
echo "  Total : ${TOTAL}"
echo "  PASS  : ${PASS_COUNT}"
echo "  FAIL  : ${FAIL_COUNT}"
echo "  WARN  : ${WARN_COUNT}"
echo ""

if [ "${FAIL_COUNT}" -eq 0 ] && [ "${WARN_COUNT}" -eq 0 ]; then
  echo "  RESULT: ALL BACKUP CHECKS PASSED"
  echo "============================================"
  exit 0
elif [ "${FAIL_COUNT}" -eq 0 ]; then
  echo "  RESULT: PASSED WITH ${WARN_COUNT} WARNING(S)"
  echo "============================================"
  exit 0
else
  echo "  RESULT: ${FAIL_COUNT} BACKUP CHECK(S) FAILED"
  echo "============================================"
  exit 1
fi
