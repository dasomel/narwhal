#!/bin/bash
# R278 (static): scripts/test/verify-backup.sh must name every Velero and CNPG resource with its API group.
# The bare short names are ambiguous on a cluster that also runs Chaos Mesh and CloudNativePG:
# `schedule` resolves to chaos-mesh.org and `backup`/`scheduledbackup` to postgresql.cnpg.io, so a live run
# reported "No Velero schedules found" although velero.io schedules existed (2026-10-19).
# Strict: any `get|describe|delete|wait` of an unqualified backup/schedule/restore/backupstoragelocation/
# backuprepository/podvolumebackup/scheduledbackup (singular or plural) fails. A self-test proves the
# matcher is not vacuous. Run from the repo root; optional $1 = file to check (used to demonstrate a failure).
set -euo pipefail

re='(get|describe|delete|wait)[[:space:]]+(backups?|schedules?|restores?|backupstoragelocations?|backuprepositor(y|ies)|podvolumebackups?|scheduledbackups?)([[:space:]]|$)'

f=${1:-scripts/test/verify-backup.sh}

# Self-test: the matcher must flag each unqualified form and accept the qualified one.
for n in 'get schedule -n x' 'get backup foo' 'get restore r' 'get scheduledbackup' 'get backuprepository' 'get backupstoragelocation default'; do
  echo "kube_get $n" | grep -qE "$re" || { echo "self-test: '$n' not flagged" >&2; exit 1; }
done
if echo 'kube_get get schedules.velero.io -n x' | grep -qE "$re"; then
  echo "self-test: qualified name flagged" >&2; exit 1
fi

bad=$(grep -nE "$re" "$f" || true)
if [ -n "$bad" ]; then
  echo "unqualified resource name in $f:" >&2
  echo "$bad" >&2
  exit 1
fi
grep -Fq 'get schedules.velero.io ' "$f" || { echo "no qualified velero schedules lookup in $f" >&2; exit 1; }
grep -Fq 'get backups.postgresql.cnpg.io ' "$f" || { echo "no qualified cnpg backups lookup in $f" >&2; exit 1; }
