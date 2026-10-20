#!/usr/bin/env bash
set -euo pipefail
umask 077

# Operator helper for the OpenBao snapshot backup (#431, gitops/resources/openbao-backup.yaml).
# Generates a self-signed RSA-4096 certificate + private key LOCALLY and applies ONLY the
# certificate (public) as ConfigMap storage/openbao-backup-recipient. The job encrypts every raft
# snapshot to that certificate with `openssl cms -encrypt`; only the private key can decrypt.
#
# The private key is written to the path you give and nowhere else: it is never printed, never put
# in a Secret/ConfigMap, never sent to the cluster. Store it OFFLINE. Losing it makes every
# snapshot unrecoverable. By default the key is protected by a passphrase that openssl asks you for
# (twice); you need that passphrase again to decrypt. --no-passphrase writes an unencrypted key.
#
# The job refuses an expired certificate (fail closed), so a certificate expiring after DAYS means
# backups stop until you re-run this script with a new key. Old snapshots need the OLD key: keep it.
#
# usage: openbao-backup-keygen.sh [--no-passphrase] <private-key-output-path> [cert-output-path]
#   DAYS=3650  validity in days (default 10 years)
#   CONTEXT    kubectl context (optional)
NODES=()
POS=()
for arg in "$@"; do
  case "${arg}" in
    --no-passphrase) NODES=(-nodes) ;;
    *) POS+=("${arg}") ;;
  esac
done
KEY_OUT="${POS[0]:-}"
CERT_OUT="${POS[1]:-${KEY_OUT:+${KEY_OUT%.*}.cert.pem}}"
DAYS="${DAYS:-3650}"
if [[ -z "${KEY_OUT}" ]]; then
  echo "usage: $0 [--no-passphrase] <private-key-output-path> [cert-output-path]" >&2
  exit 2
fi
[[ "${DAYS}" =~ ^[0-9]+$ ]] || { echo "DAYS must be a number" >&2; exit 2; }
for out in "${KEY_OUT}" "${CERT_OUT}"; do
  if [[ -e "${out}" ]]; then
    echo "refusing to overwrite existing file: ${out}" >&2
    exit 1
  fi
done
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
KUBECTL=(kubectl)
[[ -n "${CONTEXT:-}" ]] && KUBECTL+=(--context "${CONTEXT}")

if (( ${#NODES[@]} == 0 )); then
  echo "The private key will be encrypted with a passphrase; openssl asks for it now."
else
  echo "WARNING: --no-passphrase: the private key file is NOT encrypted; protect the file itself." >&2
fi
openssl req -x509 -newkey rsa:4096 -sha256 -days "${DAYS}" ${NODES[@]+"${NODES[@]}"} \
  -subj "/CN=narwhal-openbao-backup-recipient" \
  -keyout "${KEY_OUT}" -out "${CERT_OUT}"
chmod 600 "${KEY_OUT}"
chmod 644 "${CERT_OUT}"

# Only the certificate goes to the cluster; a PEM containing a private key is refused.
if grep -q 'PRIVATE KEY' "${CERT_OUT}"; then
  echo "BUG: certificate file contains a private key; not applying" >&2
  exit 1
fi
"${KUBECTL[@]}" create configmap openbao-backup-recipient -n storage \
  --from-file=recipient.pem="${CERT_OUT}" --dry-run=client -o yaml \
  | "${KUBECTL[@]}" apply -f - >/dev/null

FPR=$(openssl x509 -in "${CERT_OUT}" -noout -fingerprint -sha256 | sed 's/^.*=//')
echo "ConfigMap storage/openbao-backup-recipient applied (certificate only, expires: $(openssl x509 -in "${CERT_OUT}" -noout -enddate | cut -d= -f2))."
echo
echo "Pin this certificate so a swapped ConfigMap makes the job fail: set RECIPIENT_FPR in"
echo "gitops/resources/openbao-backup.yaml (CronJob env) through Git to:"
echo "  RECIPIENT_FPR=${FPR}"
echo
echo "!!! PRIVATE KEY: ${KEY_OUT}"
echo "!!! Store it OFFLINE (not in the cluster, not in Git, not in a Secret)."
echo "!!! Losing it makes every OpenBao snapshot UNRECOVERABLE."
