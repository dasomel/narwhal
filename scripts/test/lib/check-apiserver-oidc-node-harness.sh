#!/bin/bash
# R290b: behavioural harness for scripts/cluster/11-4b-apiserver-oidc-node.sh (D-APISERVER-OIDC-ALL).
# Runs the real node script without sudo against a temporary kubeadm-style kube-apiserver.yaml,
# a stubbed readyz and a stubbed kube-apiserver PID (APISERVER_PID_CMD). The default PID stub is
# a simulated kubelet: the PID is a checksum of the manifest, so it changes exactly when the
# manifest content changes, which is when kubelet recreates the static pod. `date` is stubbed to
# a fixed stamp so every backup in the run collides on its name.
#   1. Empty CA file fails closed without touching the manifest or creating a backup.
#   2. Non-PEM / invalid CA file fails closed without touching the manifest.
#   3. First valid run installs the CA, writes a .pre-oidc-<UTC> backup OUTSIDE the manifests
#      directory, leaves no stray files inside manifests/, and adds the exact 7 --oidc-* flags.
#   4. Second run with the same CA + issuer is an idempotent no-op (still 7 flags, still 1 backup).
#   5. Rotated CA with flags already present updates the CA and the manifest's
#      narwhal.dasomel.io/oidc-ca-sha256 annotation (content change -> kubelet restart), nothing
#      else, and first writes its own .pre-oidc-<UTC> backup (never overwriting the earlier one).
#   5b. A manifest from before the annotation (flags, no annotation) gets it once (with a
#      backup), then is a no-op (no backup).
#   6. Unhealthy readyz fails closed after bounded retries.
#   7. A kube-apiserver PID that never changes fails closed after the bounded PID wait on the
#      first-apply path, even though readyz answers ok at once (the old process still serves).
#   7b. Same on the CA/annotation update path.
#   8. A PID that changes only after a few lookups is waited for, and readyz is polled only
#      after the PID changed.
#   9. No PID before the change (stub prints nothing) falls back to the sleep + readyz wait.
#   10. Flag-mismatch path: --oidc-issuer-url present but a required flag missing (or a different
#      issuer) fails closed, naming the flag, with the manifest untouched and no backup.
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
backup_count() { find "${BACKUP_DIR}" -maxdepth 1 -name 'kube-apiserver.yaml.pre-oidc-*' | wc -l | tr -d ' '; }

# Fixed UTC stamp: every backup of the run asks for the same name, so the never-overwrite suffix
# (-2, -3, ...) is exercised deterministically rather than only on a same-second race. Any other
# `date` call passes through to the real one.
FIXED_STAMP="20260101T000000Z"
REAL_DATE="$(command -v date)"
mkdir -p "${tmp}/stubbin"
cat > "${tmp}/stubbin/date" <<EOF
#!/bin/bash
if [ "\$*" = "-u +%Y%m%dT%H%M%SZ" ]; then echo "${FIXED_STAMP}"; else exec "${REAL_DATE}" "\$@"; fi
EOF
chmod +x "${tmp}/stubbin/date"

# Simulated kubelet: the kube-apiserver PID follows the manifest content.
PID_FOLLOWS_MANIFEST="cksum < '${MANIFEST}' | cut -d' ' -f1"

run_node_script() {
  local ca_src="$1" readyz_out="${2:-ok}" pid_cmd="${3:-${PID_FOLLOWS_MANIFEST}}" pid_attempts="${4:-3}"
  PATH="${tmp}/stubbin:${PATH}" \
  APISERVER_MANIFEST="${MANIFEST}" \
  OIDC_CA_DEST="${CA_DEST}" \
  APISERVER_BACKUP_DIR="${BACKUP_DIR}" \
  APISERVER_PID_CMD="${pid_cmd}" \
  PID_CHANGE_MAX_ATTEMPTS="${pid_attempts}" \
  PID_CHANGE_SLEEP=0 \
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

# Only kube-apiserver.yaml may exist inside manifests/ (kubelet keys static pods by
# namespace/name, so a backup there is a same-name copy that conflicts with the real manifest).
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
[ "${backup_files[0]}" = "${BACKUP_DIR}/kube-apiserver.yaml.pre-oidc-${FIXED_STAMP}" ] || {
  echo "FAIL: case 3 backup is not named kube-apiserver.yaml.pre-oidc-<UTC stamp>: ${backup_files[0]}"
  exit 1
}
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
grep -Fq "kube-apiserver restarted (pid" <<<"${out1}" || {
  echo "FAIL: case 3 did not wait for the kube-apiserver PID to change: ${out1}"
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
grep -Fq "kube-apiserver restarted (pid" <<<"${out3}" || {
  echo "FAIL: case 5 did not wait for the kube-apiserver PID to change: ${out3}"
  exit 1
}
# The annotation path writes its own backup (the escape hatch must exist on a cluster that already
# had the flags) and never overwrites the earlier one.
[ "$(backup_count)" = "2" ] || {
  echo "FAIL: case 5 expected 2 backups after the annotation update, found $(backup_count)"
  exit 1
}
cmp -s "${BACKUP_DIR}/kube-apiserver.yaml.pre-oidc-${FIXED_STAMP}-2" "${tmp}/after-run1.yaml" || {
  echo "FAIL: case 5 backup is not the manifest as it was before the annotation update"
  exit 1
}
cmp -s "${BACKUP_DIR}/kube-apiserver.yaml.pre-oidc-${FIXED_STAMP}" "${tmp}/original-manifest.yaml" || {
  echo "FAIL: case 5 overwrote the earlier backup"
  exit 1
}

# 5b. Pre-annotation manifest (flags, no annotation, same CA) -> annotation added once, then no-op.
yq -i 'del(.metadata.annotations."narwhal.dasomel.io/oidc-ca-sha256")' "${MANIFEST}"
cp "${MANIFEST}" "${tmp}/before-5b.yaml"
out4="$(run_node_script "${tmp}/ca2.crt" "ok" 2>&1)"
[ "$(ann)" = "$(sha "${tmp}/ca2.crt")" ] || {
  echo "FAIL: case 5b did not add the annotation to a pre-annotation manifest (got '$(ann)'): ${out4}"
  exit 1
}
cmp -s "${BACKUP_DIR}/kube-apiserver.yaml.pre-oidc-${FIXED_STAMP}-3" "${tmp}/before-5b.yaml" || {
  echo "FAIL: case 5b did not back up the pre-annotation manifest before adding the annotation (backups: $(backup_count))"
  exit 1
}
out5="$(run_node_script "${tmp}/ca2.crt" "ok" 2>&1)"
grep -Fq "no-op" <<<"${out5}" || {
  echo "FAIL: case 5b second run was not a no-op: ${out5}"
  exit 1
}
[ "$(backup_count)" = "3" ] || {
  echo "FAIL: case 5b no-op run wrote a backup (now $(backup_count))"
  exit 1
}

# 6. Unhealthy readyz -> bounded wait exits non-zero.
cp "${tmp}/original-manifest.yaml" "${MANIFEST}"
if run_node_script "${tmp}/ca1.crt" "unhealthy" >/dev/null 2>&1; then
  echo "FAIL: case 6 (readyz!=ok) succeeded, expected non-zero exit"
  exit 1
fi

# State for the restart-detection cases: flags + the ca1 annotation, CA ca1 installed, so a run
# with ca2 takes the CA/annotation update path.
with_flags_ca1() {
  cp "${tmp}/original-manifest.yaml" "${MANIFEST}"
  run_node_script "${tmp}/ca1.crt" "ok" >/dev/null 2>&1 || {
    echo "FAIL: setup (first apply with ca1) failed"
    exit 1
  }
}

# 7. The kube-apiserver PID never changes (kubelet has not restarted it): fail closed after the
#    bounded PID wait although readyz answers ok at once, because the OLD process still serves.
cp "${tmp}/original-manifest.yaml" "${MANIFEST}"
if out6="$(run_node_script "${tmp}/ca1.crt" "ok" "echo 4242" 2>&1)"; then
  echo "FAIL: case 7 (PID unchanged, first apply) succeeded, expected non-zero exit: ${out6}"
  exit 1
fi
grep -Fq "was not restarted by kubelet" <<<"${out6}" || {
  echo "FAIL: case 7 did not report the missing restart: ${out6}"
  exit 1
}
if grep -Fq "readyz=ok" <<<"${out6}"; then
  echo "FAIL: case 7 accepted readyz before the kube-apiserver process was replaced: ${out6}"
  exit 1
fi

# 7b. The same on the CA/annotation update path.
with_flags_ca1
if out7="$(run_node_script "${tmp}/ca2.crt" "ok" "echo 4242" 2>&1)"; then
  echo "FAIL: case 7b (PID unchanged, annotation update) succeeded, expected non-zero exit: ${out7}"
  exit 1
fi
grep -Fq "updating narwhal.dasomel.io/oidc-ca-sha256" <<<"${out7}" || {
  echo "FAIL: case 7b did not take the annotation update path: ${out7}"
  exit 1
}
grep -Fq "was not restarted by kubelet" <<<"${out7}" || {
  echo "FAIL: case 7b did not report the missing restart: ${out7}"
  exit 1
}
if grep -Fq "readyz=ok" <<<"${out7}"; then
  echo "FAIL: case 7b accepted readyz before the kube-apiserver process was replaced: ${out7}"
  exit 1
fi

# 8. The old process stays visible for two lookups after the manifest changed: the script keeps
#    waiting, and readyz is polled only after the PID has changed (4 lookups in total: 1 before
#    the change, 3 after it, none once the new PID was seen).
cat > "${tmp}/pid-delayed.sh" <<EOF
#!/bin/bash
n=\$(cat "${tmp}/pid-calls" 2>/dev/null || echo 0)
n=\$((n + 1))
echo "\${n}" > "${tmp}/pid-calls"
if [ "\${n}" -le 3 ]; then echo 4242; else echo 4343; fi
EOF
chmod +x "${tmp}/pid-delayed.sh"
with_flags_ca1
rm -f "${tmp}/pid-calls"
if ! out8="$(run_node_script "${tmp}/ca2.crt" "ok" "bash ${tmp}/pid-delayed.sh" 5 2>&1)"; then
  echo "FAIL: case 8 (PID changes after a delay) failed, expected success: ${out8}"
  exit 1
fi
grep -Fq "Waiting for kubelet to restart kube-apiserver (pid 4242) (2/5)" <<<"${out8}" || {
  echo "FAIL: case 8 did not keep waiting while the old PID was visible: ${out8}"
  exit 1
}
grep -Fq "kube-apiserver restarted (pid 4242 -> 4343)" <<<"${out8}" || {
  echo "FAIL: case 8 did not report the PID change: ${out8}"
  exit 1
}
restarted_line="$(grep -n "kube-apiserver restarted (pid" <<<"${out8}" | head -n 1 | cut -d: -f1)"
ready_line="$(grep -n "readyz=ok" <<<"${out8}" | head -n 1 | cut -d: -f1)"
if [ -z "${restarted_line}" ] || [ -z "${ready_line}" ] || [ "${restarted_line}" -ge "${ready_line}" ]; then
  echo "FAIL: case 8 polled readyz before the PID changed (restarted line '${restarted_line}', readyz line '${ready_line}'): ${out8}"
  exit 1
fi
[ "$(cat "${tmp}/pid-calls")" = "4" ] || {
  echo "FAIL: case 8 expected 4 PID lookups, got $(cat "${tmp}/pid-calls")"
  exit 1
}

# 9. No PID before the change (the stub prints nothing, as without pgrep): fall back to the
#    previous sleep + readyz wait and say so.
with_flags_ca1
if ! out9="$(run_node_script "${tmp}/ca2.crt" "ok" "true" 2>&1)"; then
  echo "FAIL: case 9 (no PID before the change) failed, expected success: ${out9}"
  exit 1
fi
grep -Fq "no kube-apiserver PID found" <<<"${out9}" || {
  echo "FAIL: case 9 did not report the PID-less fallback: ${out9}"
  exit 1
}
grep -Fq "readyz=ok" <<<"${out9}" || {
  echo "FAIL: case 9 did not wait for readyz in the fallback: ${out9}"
  exit 1
}
[ "$(ann)" = "$(sha "${tmp}/ca2.crt")" ] || {
  echo "FAIL: case 9 did not move the annotation to the rotated CA (got '$(ann)')"
  exit 1
}

# 10. Flag-mismatch path (#505, LOW from the #500 review): --oidc-issuer-url is present but
#     another required flag is missing, or the issuer differs. The script must fail closed with
#     the named flag, leave the manifest byte-identical and write no backup (it never half-fixes).
with_flags_ca1
yq -i '.spec.containers[0].command |= map(select(. != "--oidc-groups-claim=groups"))' "${MANIFEST}"
cp "${MANIFEST}" "${tmp}/mismatch-manifest.yaml"
backups_before="$(backup_count)"
if out10="$(run_node_script "${tmp}/ca1.crt" "ok" 2>&1)"; then
  echo "FAIL: case 10 (missing --oidc-groups-claim) succeeded, expected non-zero exit: ${out10}"
  exit 1
fi
grep -Fq "is missing required flag --oidc-groups-claim=groups" <<<"${out10}" || {
  echo "FAIL: case 10 did not name the missing flag: ${out10}"
  exit 1
}
cmp -s "${MANIFEST}" "${tmp}/mismatch-manifest.yaml" || {
  echo "FAIL: case 10 modified the manifest on the mismatch path"
  exit 1
}
[ "$(backup_count)" = "${backups_before}" ] || {
  echo "FAIL: case 10 wrote a backup on the mismatch path"
  exit 1
}
with_flags_ca1
yq -i '.spec.containers[0].command |= map(select(. != "--oidc-issuer-url='"${ISSUER_URL}"'")) | .spec.containers[0].command += ["--oidc-issuer-url=https://other.example/realms/x"]' "${MANIFEST}"
cp "${MANIFEST}" "${tmp}/mismatch-manifest.yaml"
if out10b="$(run_node_script "${tmp}/ca1.crt" "ok" 2>&1)"; then
  echo "FAIL: case 10b (different issuer) succeeded, expected non-zero exit: ${out10b}"
  exit 1
fi
grep -Fq "is missing required flag --oidc-issuer-url=${ISSUER_URL}" <<<"${out10b}" || {
  echo "FAIL: case 10b did not name the expected issuer flag: ${out10b}"
  exit 1
}
cmp -s "${MANIFEST}" "${tmp}/mismatch-manifest.yaml" || {
  echo "FAIL: case 10b modified the manifest"
  exit 1
}

echo "OK: 11-4b-apiserver-oidc-node.sh passed 12 behavioural cases (empty CA, invalid PEM, 7-flag apply + CA annotation + external backup, idempotent re-run, CA rotation via annotation with its own backup, pre-annotation manifest, readyz timeout, PID that never changes on both restart paths, delayed PID change before readyz, no-PID fallback, flag-mismatch fails closed untouched)"
