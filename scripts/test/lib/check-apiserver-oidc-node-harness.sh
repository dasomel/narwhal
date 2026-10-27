#!/bin/bash
# R290b: behavioural harness for scripts/cluster/11-4b-apiserver-oidc-node.sh (D-APISERVER-OIDC-ALL).
# Runs the real node script without sudo against a temporary kubeadm-style kube-apiserver.yaml
# and stubbed readyz:
#   1. Empty CA file fails closed without touching the manifest or creating a backup.
#   2. Non-PEM / invalid CA file fails closed without touching the manifest.
#   3. First valid run installs the CA, writes a .pre-oidc-<UTC> backup OUTSIDE the manifests
#      directory, leaves no stray files inside manifests/, and adds the exact 7 --oidc-* flags.
#   4. Second run with the same CA + issuer is an idempotent no-op (still 7 flags, still 1 backup).
#   5. Rotated CA with flags already present updates the CA and the manifest's
#      narwhal.dasomel.io/oidc-ca-sha256 annotation (content change -> kubelet restart), nothing else.
#   5b. A manifest from before the annotation (flags, no annotation) gets it once, then is a no-op.
#   6. Unhealthy readyz fails closed after bounded retries.
set -euo pipefail

if ! command -v yq >/dev/null 2>&1; then
  echo "SKIP: yq not found on PATH; skipping R290b behavioural harness locally"
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
NODE_SCRIPT="${1:-${ROOT}/scripts/cluster/11-4b-apiserver-oidc-node.sh}"
ISSUER_URL="https://keycloak.kakao.narwhal.internal/realms/narwhal"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

mkdir -p "${tmp}/etc/kubernetes/manifests" "${tmp}/etc/kubernetes/pki"
MANIFEST="${tmp}/etc/kubernetes/manifests/kube-apiserver.yaml"
CA_DEST="${tmp}/etc/kubernetes/pki/oidc-ca.crt"
BACKUP_DIR="${tmp}/etc/kubernetes"

cat > "${MANIFEST}" <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: kube-apiserver
  namespace: kube-system
  labels:
    component: kube-apiserver
    tier: control-plane
spec:
  containers:
  - command:
    - kube-apiserver
    - --advertise-address=172.17.0.11
    - --allow-privileged=true
    - --authorization-mode=Node,RBAC
    - --client-ca-file=/etc/kubernetes/pki/ca.crt
    - --enable-admission-plugins=NodeRestriction
    - --enable-bootstrap-token-auth=true
    - --etcd-servers=https://172.17.0.10:2379,https://172.17.0.11:2379,https://172.17.0.12:2379
    - --secure-port=6443
    - --tls-cert-file=/etc/kubernetes/pki/apiserver.crt
    - --tls-private-key-file=/etc/kubernetes/pki/apiserver.key
    image: registry.k8s.io/kube-apiserver:v1.35.0
    name: kube-apiserver
  hostNetwork: true
EOF
cp "${MANIFEST}" "${tmp}/original-manifest.yaml"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "${tmp}/ca1.key" -out "${tmp}/ca1.crt" \
  -days 1 -subj "/CN=narwhal-oidc-test-ca-1" >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "${tmp}/ca2.key" -out "${tmp}/ca2.crt" \
  -days 1 -subj "/CN=narwhal-oidc-test-ca-2" >/dev/null 2>&1
: > "${tmp}/empty-ca.crt"
printf 'not-a-valid-pem-cert\n' > "${tmp}/garbage-ca.crt"

ann() { yq '.metadata.annotations."narwhal.dasomel.io/oidc-ca-sha256" // ""' "${MANIFEST}"; }
sha() { openssl dgst -sha256 -r "$1" | cut -d' ' -f1; }
cmds() { yq '.spec.containers[0].command' "$1"; }

run_node_script() {
  local ca_src="$1" readyz_out="${2:-ok}"
  APISERVER_MANIFEST="${MANIFEST}" \
  OIDC_CA_DEST="${CA_DEST}" \
  APISERVER_BACKUP_DIR="${BACKUP_DIR}" \
  READYZ_CMD="printf '%s' '${readyz_out}'" \
  READYZ_MAX_ATTEMPTS=2 \
  READYZ_SLEEP=0 \
  RESTART_INITIAL_SLEEP=0 \
  bash "${NODE_SCRIPT}" "${ISSUER_URL}" "${ca_src}"
}

# 1. Empty CA file -> fail closed without touching manifest or creating backup.
if run_node_script "${tmp}/empty-ca.crt" >/dev/null 2>&1; then
  echo "FAIL: case 1 (empty CA) succeeded, expected non-zero exit"
  exit 1
fi
cmp -s "${MANIFEST}" "${tmp}/original-manifest.yaml" || {
  echo "FAIL: case 1 (empty CA) modified ${MANIFEST}"
  exit 1
}
[ ! -f "${CA_DEST}" ] || {
  echo "FAIL: case 1 (empty CA) created ${CA_DEST}"
  exit 1
}

# 2. Garbage non-PEM CA file -> fail closed without touching manifest.
if run_node_script "${tmp}/garbage-ca.crt" >/dev/null 2>&1; then
  echo "FAIL: case 2 (invalid PEM CA) succeeded, expected non-zero exit"
  exit 1
fi
cmp -s "${MANIFEST}" "${tmp}/original-manifest.yaml" || {
  echo "FAIL: case 2 (invalid PEM CA) modified ${MANIFEST}"
  exit 1
}

# 3. First valid run -> adds exact 7 flags, backs up outside manifests/, installs CA.
out1="$(run_node_script "${tmp}/ca1.crt" "ok" 2>&1)"
cmp -s "${tmp}/ca1.crt" "${CA_DEST}" || {
  echo "FAIL: case 3 did not install CA to ${CA_DEST}"
  exit 1
}

# Only kube-apiserver.yaml may exist inside manifests/ (a backup there starts a second static pod).
manifest_entries="$(find "${tmp}/etc/kubernetes/manifests" -mindepth 1 -maxdepth 1)"
if [ "${manifest_entries}" != "${MANIFEST}" ]; then
  echo "FAIL: case 3 left extra file(s) inside manifests/: ${manifest_entries}"
  exit 1
fi

backup_files=()
while IFS= read -r bf; do
  [ -n "${bf}" ] && backup_files+=("${bf}")
done < <(find "${BACKUP_DIR}" -maxdepth 1 -name 'kube-apiserver.yaml.pre-oidc-*')
if [ "${#backup_files[@]}" -ne 1 ]; then
  echo "FAIL: case 3 expected 1 backup outside manifests/, found ${#backup_files[@]}"
  exit 1
fi
cmp -s "${backup_files[0]}" "${tmp}/original-manifest.yaml" || {
  echo "FAIL: case 3 backup does not match pre-OIDC manifest"
  exit 1
}

oidc_flags="$(yq '.spec.containers[0].command[] | select(. == "--oidc-*")' "${MANIFEST}")"
oidc_count="$(grep -c '^--oidc-' <<<"${oidc_flags}" || true)"
if [ "${oidc_count}" -ne 7 ]; then
  echo "FAIL: case 3 expected 7 --oidc-* flags, got ${oidc_count}: ${oidc_flags}"
  exit 1
fi
for expected in \
  "--oidc-issuer-url=${ISSUER_URL}" \
  "--oidc-client-id=kubernetes" \
  "--oidc-username-claim=preferred_username" \
  "--oidc-groups-claim=groups" \
  "--oidc-username-prefix=oidc:" \
  "--oidc-groups-prefix=oidc:" \
  "--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt"; do
  grep -qxF -- "${expected}" <<<"${oidc_flags}" || {
    echo "FAIL: case 3 missing expected flag '${expected}'"
    exit 1
  }
done
grep -Fq "Applied 7 OIDC flags" <<<"${out1}" || {
  echo "FAIL: case 3 output missing 'Applied 7 OIDC flags': ${out1}"
  exit 1
}
[ "$(ann)" = "$(sha "${tmp}/ca1.crt")" ] || {
  echo "FAIL: case 3 did not record the CA sha256 annotation (got '$(ann)')"
  exit 1
}
cp "${MANIFEST}" "${tmp}/after-run1.yaml"

# 4. Second run with same CA -> idempotent no-op.
out2="$(run_node_script "${tmp}/ca1.crt" "ok" 2>&1)"
cmp -s "${MANIFEST}" "${tmp}/after-run1.yaml" || {
  echo "FAIL: case 4 (second run) modified ${MANIFEST}"
  exit 1
}
backup_count2="$(find "${BACKUP_DIR}" -maxdepth 1 -name 'kube-apiserver.yaml.pre-oidc-*' | wc -l | tr -d ' ')"
if [ "${backup_count2}" != "1" ]; then
  echo "FAIL: case 4 created an extra backup (${backup_count2})"
  exit 1
fi
grep -Fq "no-op" <<<"${out2}" || {
  echo "FAIL: case 4 output did not report no-op: ${out2}"
  exit 1
}

# 5. Rotated CA when flags already exist -> CA file and the annotation change, nothing else.
out3="$(run_node_script "${tmp}/ca2.crt" "ok" 2>&1)"
cmp -s "${tmp}/ca2.crt" "${CA_DEST}" || {
  echo "FAIL: case 5 did not update ${CA_DEST} to rotated CA"
  exit 1
}
[ "$(ann)" = "$(sha "${tmp}/ca2.crt")" ] || {
  echo "FAIL: case 5 did not move the annotation to the rotated CA (got '$(ann)')"
  exit 1
}
[ "$(cmds "${MANIFEST}")" = "$(cmds "${tmp}/after-run1.yaml")" ] || {
  echo "FAIL: case 5 changed the command list during CA rotation"
  exit 1
}
grep -Fq "updating narwhal.dasomel.io/oidc-ca-sha256" <<<"${out3}" || {
  echo "FAIL: case 5 did not report the restart-triggering annotation update: ${out3}"
  exit 1
}

# 5b. Pre-annotation manifest (flags, no annotation, same CA) -> annotation added once, then no-op.
yq -i 'del(.metadata.annotations."narwhal.dasomel.io/oidc-ca-sha256")' "${MANIFEST}"
out4="$(run_node_script "${tmp}/ca2.crt" "ok" 2>&1)"
[ "$(ann)" = "$(sha "${tmp}/ca2.crt")" ] || {
  echo "FAIL: case 5b did not add the annotation to a pre-annotation manifest (got '$(ann)'): ${out4}"
  exit 1
}
out5="$(run_node_script "${tmp}/ca2.crt" "ok" 2>&1)"
grep -Fq "no-op" <<<"${out5}" || {
  echo "FAIL: case 5b second run was not a no-op: ${out5}"
  exit 1
}

# 6. Unhealthy readyz -> bounded wait exits non-zero.
cp "${tmp}/original-manifest.yaml" "${MANIFEST}"
if run_node_script "${tmp}/ca1.crt" "unhealthy" >/dev/null 2>&1; then
  echo "FAIL: case 6 (readyz!=ok) succeeded, expected non-zero exit"
  exit 1
fi

echo "OK: 11-4b-apiserver-oidc-node.sh passed 7 behavioural cases (empty CA, invalid PEM, 7-flag apply + CA annotation + external backup, idempotent re-run, CA rotation via annotation, pre-annotation manifest, readyz timeout)"
