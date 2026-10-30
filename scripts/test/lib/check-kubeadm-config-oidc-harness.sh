#!/bin/bash
# R297: behavioural harness for scripts/cluster/11-4c-kubeadm-config-oidc.sh (D-KUBEADM-CONFIG-OIDC, #505).
# Runs the real helper without sudo against a stub kubectl whose "kubeadm-config" is a file, so
# it exercises the real yq merge on sample kubeadm.k8s.io ClusterConfiguration documents.
#   1. v1beta4 without apiServer.extraArgs: exactly the 7 flags are added, the rest is untouched,
#      one backup of the original is written, and only data.ClusterConfiguration is patched
#      (pinned to the resourceVersion that was read).
#   2. Unrelated extraArgs and extraVolumes are preserved with their order.
#   3. Stale oidc-* values (incl. a duplicate) are replaced: still exactly 7 flags.
#   4. A second run is a no-op: no patch, no new backup.
#   5. Malformed YAML, wrong kind, unsupported apiVersion, a map extraArgs under v1beta4, two
#      documents and an empty ClusterConfiguration exit non-zero without a patch or a backup.
#   6. v1beta3 (extraArgs is a map) is merged as a map.
#   7. A failing patch exits non-zero; a patch the API silently dropped is caught by the re-read.
#   8. A readable local apiserver manifest without a volumeMount covering /etc/kubernetes/pki is
#      refused; with the default pki mount it is accepted.
#   9. --dry-run and a non-https issuer write nothing.
#   10/11. Wiring (static): 11-4, Kakao step, verify gate, and the later-join CA staging in
#      02-join-control-plane.sh / distribute-join-kakao.sh (copy before join, refuse without it).
# Usage: check-kubeadm-config-oidc-harness.sh [helper]   |   --mutation-verify
#   --mutation-verify runs this harness against broken copies of the helper; each must fail.
set -euo pipefail

if [ "${1:-}" = "--mutation-verify" ]; then
  exec python3 - "${BASH_SOURCE[0]}" <<'PYEOF'
import os, subprocess, sys, tempfile

harness = os.path.abspath(sys.argv[1])
root = os.path.abspath(os.path.join(os.path.dirname(harness), "../../.."))
helper = open(os.path.join(root, "scripts/cluster/11-4c-kubeadm-config-oidc.sh")).read()
JOIN = "scripts/cluster/02-join-control-plane.sh"
DIST = "scripts/cloud/distribute-join-kakao.sh"
WIRED = [
    "scripts/cluster/11-4-keycloak-apiserver.sh", "scripts/cloud/apply-apiserver-oidc-kakao.sh",
    "scripts/test/verify-cluster.sh", "scripts/cluster/02-init-cluster.sh", JOIN, DIST,
]
# (name, relative file, exact anchor, replacement): wiring mutations run against a copied tree.
WIRING_MUTATIONS = [
    ("join: CA copy removed", JOIN, "sudo install -o root -g root -m 0644 /tmp/oidc-ca.crt", "sudo true /tmp/oidc-ca.crt"),
    ("join: CA fetch from master-1 removed", JOIN, ":/etc/kubernetes/pki/oidc-ca.crt", ":/etc/kubernetes/pki/other.crt"),
    ("join: refuse path removed", JOIN, "could not be obtained — this node's apiserver would crashloop after join. Not joining\" >&2\n    exit 1", "could not be obtained\" >&2\n    true"),
    ("join: unreadable config no longer refused", JOIN, "not joining\" >&2\n    exit 1", "not joining\" >&2\n    true"),
    ("dist: CA staging removed", DIST, 'place "${ip}" oidc-ca.crt /tmp/oidc-ca.crt', "true"),
    ("dist: refuse path removed", DIST, "is missing or not a valid certificate; a joined master would crashloop\" >&2\n    exit 1", "is missing\" >&2\n    true"),
    ("verify: v1beta3 form dropped", "scripts/test/verify-cluster.sh", "kubeadm.k8s.io/v1beta3)", "kubeadm.k8s.io/v1beta9)"),
]

# (name, exact anchor in the helper, replacement)
MUTATIONS = [
    ("stale-oidc-entries-kept", 'map(select(.name | test("^oidc-") | not))', "map(.)"),
    ("no-noop-short-circuit", 'if [ "$(oidc_pairs "${cur_file}")" = "${EXPECTED}" ]; then', "if false; then"),
    ("input-not-validated", 'validate_input "${cur_file}"\n', "true\n"),
    ("merge-drops-unrelated-extraargs", "((.apiServer.extraArgs // []) | map", "(([]) | map"),
    # The guard is the only thing that stops a lossy merge reaching the API: break both.
    ("lossy-merge-and-guard-removed",
     ['[ "$(non_oidc_json "${cur_file}")" = "$(non_oidc_json "${new_file}")" ]', "((.apiServer.extraArgs // []) | map"],
     ["true", "(([]) | map"]),
    ("patch-not-pinned-to-resourceversion", ".metadata.resourceVersion = strenv(RV) | ", ""),
    ("reread-after-patch-removed", '[ "$(oidc_pairs "${after_file}")" = "${EXPECTED}" ]', "true"),
    ("pki-mount-guard-removed", '[ "${covered}" = "true" ] ||', "true ||"),
    ("dry-run-writes", 'if [ "${DRY_RUN}" = "true" ]; then', "if false; then"),
    ("http-issuer-accepted", "  https://*) ;;\n  *) fail", "  *) ;;\n  *) fail"),
    ("wrong-flag-value", '"oidc-username-claim=preferred_username"', '"oidc-username-claim=sub"'),
    ("backup-skipped", 'cp "${cur_file}" "${backup_path}" && [ -s "${backup_path}" ]', "true"),
]
rc = 0
with tempfile.TemporaryDirectory() as d:
    for name, anchor, repl in MUTATIONS:
        anchors = anchor if isinstance(anchor, list) else [anchor]
        repls = repl if isinstance(repl, list) else [repl]
        if any(a not in helper for a in anchors):
            print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
            rc = 1
            continue
        mutated = helper
        for a, r_ in zip(anchors, repls):
            mutated = mutated.replace(a, r_)
        path = os.path.join(d, "helper.sh")
        open(path, "w").write(mutated)
        r = subprocess.run(["bash", harness, path], capture_output=True)
        if r.returncode == 0:
            print(f"FAIL mutation '{name}' was not caught")
            rc = 1
        else:
            print(f"OK mutation caught: {name}")

import shutil
for name, rel, anchor, repl in WIRING_MUTATIONS:
    with tempfile.TemporaryDirectory() as d:
        for f in WIRED:
            os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True)
            shutil.copy(os.path.join(root, f), os.path.join(d, f))
        target = os.path.join(d, rel)
        text = open(target).read()
        if anchor not in text:
            print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
            rc = 1
            continue
        open(target, "w").write(text.replace(anchor, repl, 1))
        r = subprocess.run(["bash", harness], capture_output=True, env=dict(os.environ, R297_ROOT=d))
        if r.returncode == 0:
            print(f"FAIL mutation '{name}' was not caught")
            rc = 1
        else:
            print(f"OK mutation caught: {name}")
sys.exit(rc)
PYEOF
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "SKIP: yq not found on PATH; skipping R297 behavioural harness locally"
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HELPER="${1:-${ROOT}/scripts/cluster/11-4c-kubeadm-config-oidc.sh}"
ISSUER="https://keycloak.kakao.narwhal.internal/realms/narwhal"
# Wiring checks read the other scripts from here; --mutation-verify points it at a mutated copy.
WROOT="${R297_ROOT:-${ROOT}}"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin" "${tmp}/bk"
export STUB_DIR="${tmp}"

# kubectl stub: "get cm" prints data.ClusterConfiguration or resourceVersion; "patch" applies the
# merge patch file to cc.yaml (unless STUB_PATCH_FAIL / STUB_PATCH_DROP) and keeps the patch.
cat > "${tmp}/bin/kubectl" <<'STUB'
#!/bin/bash
set -euo pipefail
case "$*" in
  *"get cm kubeadm-config"*)
    case "$*" in
      *resourceVersion*) echo 4242 ;;
      *) cat "${STUB_DIR}/cc.yaml" ;;
    esac
    ;;
  *" patch cm kubeadm-config "*)
    [ -z "${STUB_PATCH_FAIL:-}" ] || { echo "stub: patch refused" >&2; exit 1; }
    pf="${*: -1}"
    n="$(find "${STUB_DIR}" -maxdepth 1 -name 'patch-*.json' | wc -l | tr -d ' ')"
    cp "${pf}" "${STUB_DIR}/patch-${n}.json"
    if [ -z "${STUB_PATCH_DROP:-}" ]; then
      yq -r '.data.ClusterConfiguration' "${pf}" > "${STUB_DIR}/cc.yaml"
    fi
    ;;
  *) echo "stub: unexpected kubectl $*" >&2; exit 2 ;;
esac
STUB
chmod +x "${tmp}/bin/kubectl"

PASS=0
fail_case() { echo "FAIL: $*" >&2; exit 1; }
ok() { PASS=$((PASS + 1)); echo "ok: $*"; }
patches() { find "${tmp}" -maxdepth 1 -name 'patch-*.json' | wc -l | tr -d ' '; }
backups() { find "${tmp}/bk" -type f | wc -l | tr -d ' '; }
# Replace the stub's ClusterConfiguration with stdin and forget earlier patches/backups.
reset() {
  rm -f "${tmp}"/patch-*.json "${tmp}"/bk/*
  cat > "${tmp}/cc.yaml"
}
run() {
  KUBECTL="${tmp}/bin/kubectl" KUBEADM_CONFIG_BACKUP_DIR="${tmp}/bk" \
    APISERVER_MANIFEST="${APISERVER_MANIFEST:-${tmp}/no-such-manifest}" \
    bash "${HELPER}" "$@"
}
args_of() { yq '.apiServer.extraArgs[] | .name + "=" + .value' "${tmp}/cc.yaml"; }
# No grep -q on a pipe: with pipefail an early close would turn into a false failure.
has_arg() { args_of | grep -xF -- "$1" >/dev/null; }
oidc_count() { yq '[.apiServer.extraArgs[] | select(.name | test("^oidc-"))] | length' "${tmp}/cc.yaml"; }

V4_BASE='apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: v1.35.7
controlPlaneEndpoint: "172.17.0.100:6443"
networking:
  podSubnet: 10.244.0.0/16
'

# 1. no extraArgs at all
printf '%s' "${V4_BASE}" | reset
before="$(yq -o=json -I=0 'del(.apiServer) | sort_keys(..)' "${tmp}/cc.yaml")"
run "${ISSUER}" >/dev/null || fail_case "case 1: helper failed"
[ "$(oidc_count)" = "7" ] || fail_case "case 1: expected 7 oidc flags"
has_arg "oidc-issuer-url=${ISSUER}" || fail_case "case 1: issuer missing"
for want in oidc-client-id=kubernetes oidc-username-claim=preferred_username oidc-groups-claim=groups \
  oidc-username-prefix=oidc: oidc-groups-prefix=oidc: oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt; do
  has_arg "${want}" || fail_case "case 1: missing ${want}"
done
[ "$(yq -o=json -I=0 'del(.apiServer) | sort_keys(..)' "${tmp}/cc.yaml")" = "${before}" ] || fail_case "case 1: non-apiServer content changed"
[ "$(yq '.apiServer | keys | .[]' "${tmp}/cc.yaml")" = "extraArgs" ] || fail_case "case 1: apiServer gained keys other than extraArgs"
[ "$(patches)" = "1" ] || fail_case "case 1: expected exactly 1 patch"
[ "$(backups)" = "1" ] || fail_case "case 1: expected exactly 1 backup"
cmp -s "${tmp}"/bk/kubeadm-config.ClusterConfiguration.pre-oidc-*.yaml <(printf '%s' "${V4_BASE}") || fail_case "case 1: backup is not the original"
[ "$(yq -o=json -I=0 'keys | sort' "${tmp}/patch-0.json")" = '["data","metadata"]' ] || fail_case "case 1: patch has other top-level keys"
[ "$(yq -o=json -I=0 '.data | keys' "${tmp}/patch-0.json")" = '["ClusterConfiguration"]' ] || fail_case "case 1: patch touches other data keys"
[ "$(yq -o=json -I=0 '.metadata | keys' "${tmp}/patch-0.json")" = '["resourceVersion"]' ] || fail_case "case 1: patch touches other metadata"
[ "$(yq -r '.metadata.resourceVersion' "${tmp}/patch-0.json")" = "4242" ] || fail_case "case 1: patch not pinned to the read resourceVersion"
ok "1 no extraArgs -> exactly 7 flags, rest untouched, one backup, ClusterConfiguration-only patch pinned to resourceVersion"

# 4. idempotent
run "${ISSUER}" >/dev/null || fail_case "case 4: second run failed"
{ [ "$(patches)" = "1" ] && [ "$(backups)" = "1" ]; } || fail_case "case 4: second run wrote again"
ok "4 second run is a no-op (no patch, no new backup)"

# 2/3. unrelated args and volumes preserved in order; stale values replaced
cat <<'DOC' | reset
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: v1.35.7
apiServer:
  certSANs:
  - 172.17.0.100
  extraArgs:
  - name: profiling
    value: "false"
  - name: oidc-client-id
    value: stale-client
  - name: authorization-mode
    value: Node,RBAC
  - name: oidc-issuer-url
    value: https://old.example/realms/x
  - name: oidc-client-id
    value: stale-duplicate
  - name: audit-log-path
    value: /var/log/kubernetes/audit/audit.log
  extraVolumes:
  - name: enc
    hostPath: /etc/kubernetes/enc
    mountPath: /etc/kubernetes/enc
    readOnly: true
    pathType: DirectoryOrCreate
  - name: audit-log
    hostPath: /var/log/kubernetes/audit
    mountPath: /var/log/kubernetes/audit
    pathType: DirectoryOrCreate
controllerManager:
  extraArgs:
  - name: bind-address
    value: 0.0.0.0
DOC
vol_before="$(yq -o=json -I=0 '.apiServer.extraVolumes' "${tmp}/cc.yaml")"
cm_before="$(yq -o=json -I=0 '.controllerManager' "${tmp}/cc.yaml")"
run "${ISSUER}" >/dev/null || fail_case "case 2: helper failed"
[ "$(oidc_count)" = "7" ] || fail_case "case 3: stale/duplicate oidc entries not replaced (count $(oidc_count))"
has_arg "oidc-client-id=kubernetes" || fail_case "case 3: client id not corrected"
if args_of | grep "old.example\|stale" >/dev/null; then fail_case "case 3: stale value survived"; fi
[ "$(args_of | grep -v '^oidc-' | tr '\n' ' ')" = "profiling=false authorization-mode=Node,RBAC audit-log-path=/var/log/kubernetes/audit/audit.log " ] || fail_case "case 2: unrelated extraArgs changed or reordered"
[ "$(yq -o=json -I=0 '.apiServer.extraVolumes' "${tmp}/cc.yaml")" = "${vol_before}" ] || fail_case "case 2: extraVolumes changed"
[ "$(yq -o=json -I=0 '.controllerManager' "${tmp}/cc.yaml")" = "${cm_before}" ] || fail_case "case 2: controllerManager changed"
[ "$(yq '.apiServer.certSANs[0]' "${tmp}/cc.yaml")" = "172.17.0.100" ] || fail_case "case 2: certSANs changed"
ok "2/3 unrelated extraArgs, extraVolumes, controllerManager preserved in order; stale and duplicate oidc entries replaced"
run "${ISSUER}" >/dev/null || fail_case "case 3b: re-run failed"
[ "$(patches)" = "1" ] || fail_case "case 3b: re-run patched again"
ok "3b idempotent on the merged document"

# 5. fail-closed inputs
expect_fail() {
  local name="$1"
  if run "${ISSUER}" >/dev/null 2>"${tmp}/err"; then fail_case "${name}: expected non-zero exit"; fi
  grep "^ERROR:" "${tmp}/err" >/dev/null || fail_case "${name}: no ERROR line on stderr"
  [ "$(patches)" = "0" ] || fail_case "${name}: patched despite the error"
  [ "$(backups)" = "0" ] || fail_case "${name}: wrote a backup despite the error"
  ok "5 ${name} -> non-zero, no patch, no backup"
}
printf 'apiVersion: [unclosed\nkind: : :\n  - {' | reset; expect_fail "malformed YAML"
printf 'apiVersion: kubeadm.k8s.io/v1beta4\nkind: InitConfiguration\n' | reset; expect_fail "wrong kind"
printf 'apiVersion: kubeadm.k8s.io/v1beta5\nkind: ClusterConfiguration\n' | reset; expect_fail "unsupported apiVersion"
printf 'apiVersion: kubeadm.k8s.io/v1beta4\nkind: ClusterConfiguration\napiServer:\n  extraArgs:\n    profiling: "false"\n' | reset; expect_fail "map extraArgs under v1beta4"
printf 'apiVersion: kubeadm.k8s.io/v1beta4\nkind: ClusterConfiguration\n---\napiVersion: kubeadm.k8s.io/v1beta4\nkind: ClusterConfiguration\n' | reset; expect_fail "two documents"
: | reset; expect_fail "empty ClusterConfiguration"

# 6. v1beta3 map form
cat <<'DOC' | reset
apiVersion: kubeadm.k8s.io/v1beta3
kind: ClusterConfiguration
apiServer:
  extraArgs:
    profiling: "false"
    oidc-client-id: stale
DOC
run "${ISSUER}" >/dev/null || fail_case "case 6: v1beta3 failed"
[ "$(yq '.apiServer.extraArgs | keys | map(select(test("^oidc-"))) | length' "${tmp}/cc.yaml")" = "7" ] || fail_case "case 6: expected 7 oidc keys"
[ "$(yq '.apiServer.extraArgs.profiling' "${tmp}/cc.yaml")" = "false" ] || fail_case "case 6: profiling lost"
[ "$(yq '.apiServer.extraArgs["oidc-client-id"]' "${tmp}/cc.yaml")" = "kubernetes" ] || fail_case "case 6: stale value kept"
ok "6 v1beta3 map extraArgs merged as a map"

# 7. patch failure / silently dropped patch
printf '%s' "${V4_BASE}" | reset
if STUB_PATCH_FAIL=1 run "${ISSUER}" >/dev/null 2>&1; then fail_case "case 7: patch failure exited 0"; fi
ok "7 a refused patch exits non-zero"
printf '%s' "${V4_BASE}" | reset
if STUB_PATCH_DROP=1 run "${ISSUER}" >/dev/null 2>&1; then fail_case "case 7b: dropped patch exited 0"; fi
ok "7b a patch the API did not persist is caught by the re-read"

# 8. CA mount guard
printf '%s' "${V4_BASE}" | reset
cat > "${tmp}/manifest-nomount.yaml" <<'DOC'
spec:
  containers:
  - name: kube-apiserver
    volumeMounts:
    - mountPath: /etc/ssl/certs
      name: ca-certs
DOC
cat > "${tmp}/manifest-mount.yaml" <<'DOC'
spec:
  containers:
  - name: kube-apiserver
    volumeMounts:
    - mountPath: /etc/ssl/certs
      name: ca-certs
    - mountPath: /etc/kubernetes/pki
      name: k8s-certs
DOC
if APISERVER_MANIFEST="${tmp}/manifest-nomount.yaml" run "${ISSUER}" >/dev/null 2>&1; then fail_case "case 8: no pki mount accepted"; fi
[ "$(patches)" = "0" ] || fail_case "case 8: patched without a pki mount"
APISERVER_MANIFEST="${tmp}/manifest-mount.yaml" run "${ISSUER}" >/dev/null || fail_case "case 8: pki mount refused"
[ "$(patches)" = "1" ] || fail_case "case 8: not patched with a pki mount"
ok "8 refuses without a volumeMount covering /etc/kubernetes/pki, accepts with it"

# 9. dry-run and bad issuer
printf '%s' "${V4_BASE}" | reset
run "${ISSUER}" --dry-run >/dev/null || fail_case "case 9: dry-run failed"
{ [ "$(patches)" = "0" ] && [ "$(backups)" = "0" ]; } || fail_case "case 9: dry-run wrote"
if run "http://insecure.example/realms/narwhal" >/dev/null 2>&1; then fail_case "case 9: http issuer accepted"; fi
[ "$(patches)" = "0" ] || fail_case "case 9: http issuer patched"
ok "9 --dry-run and a non-https issuer write nothing"

# 10. Wiring (static): the helper is called once from 11-4 with the issuer and a failure stops
#     Phase 2, the Kakao operator script runs it on master-1, verify-cluster.sh gates on it, and
#     02-init-cluster.sh still has no active oidc-* extraArgs (Keycloak does not exist at init).
code() { grep -v '^[[:space:]]*#' "$1"; }
code "${WROOT}/scripts/cluster/11-4-keycloak-apiserver.sh" | grep 'sudo env KUBECONFIG=.*KUBEADM_CONFIG_OIDC_SCRIPT.*OIDC_ISSUER_URL' >/dev/null \
  || fail_case "case 10: 11-4 does not call the helper with the issuer"
code "${WROOT}/scripts/cluster/11-4-keycloak-apiserver.sh" | grep -A3 'KUBEADM_CONFIG_OIDC_SCRIPT}" "${OIDC_ISSUER_URL}"' | grep 'exit 1' >/dev/null \
  || fail_case "case 10: 11-4 does not fail hard when the helper fails"
code "${WROOT}/scripts/cloud/apply-apiserver-oidc-kakao.sh" | grep 'KUBEADM_CONFIG_SCRIPT} ${OIDC_ISSUER_URL}' >/dev/null \
  || fail_case "case 10: apply-apiserver-oidc-kakao.sh does not run the helper on master-1"
code "${WROOT}/scripts/test/verify-cluster.sh" | grep 'kubeadm-config OIDC' >/dev/null \
  || fail_case "case 10: verify-cluster.sh has no kubeadm-config OIDC gate"
if code "${WROOT}/scripts/cluster/02-init-cluster.sh" | grep -E '^[[:space:]]*-?[[:space:]]*(name:[[:space:]]*)?oidc-' >/dev/null; then
  fail_case "case 10: 02-init-cluster.sh carries active oidc-* extraArgs"
fi
ok "10 wiring: 11-4 (hard fail), Kakao master-1 step, verify-cluster gate, init unchanged"

# 11. Later-joined control planes (review of PR #521): the apiserver kubeadm renders from the
#     patched ClusterConfiguration cannot start without oidc-ca.crt, and kubeadm join does not
#     copy it. 02-join-control-plane.sh must install it root:root 0644 BEFORE the join and refuse
#     to join when the config references it but it cannot be had; on Kakao the operator script
#     stages it and refuses first.
JOIN="${WROOT}/scripts/cluster/02-join-control-plane.sh"
DIST="${WROOT}/scripts/cloud/distribute-join-kakao.sh"
code "${JOIN}" | grep 'install -o root -g root -m 0644 /tmp/oidc-ca.crt' >/dev/null \
  || fail_case "case 11: 02-join does not install the OIDC CA root:root 0644"
code "${JOIN}" | grep 'vagrant@.*:/etc/kubernetes/pki/oidc-ca.crt' >/dev/null \
  || fail_case "case 11: 02-join does not fetch the OIDC CA from master-1"
code "${JOIN}" | grep -A2 'valid /tmp/oidc-ca.crt could not be obtained' | grep 'exit 1' >/dev/null \
  || fail_case "case 11: 02-join does not refuse to join without the OIDC CA"
code "${JOIN}" | grep -A2 'cannot read kube-system/kubeadm-config from master-1' | grep 'exit 1' >/dev/null \
  || fail_case "case 11: 02-join joins when kubeadm-config is unreadable"
inst_line="$(code "${JOIN}" | grep -n 'install -o root -g root -m 0644 /tmp/oidc-ca.crt' | head -n 1 | cut -d: -f1)"
join_line="$(code "${JOIN}" | grep -n 'sudo bash -c "$(cat /tmp/join-control-plane.sh)' | head -n 1 | cut -d: -f1)"
{ [ -n "${inst_line}" ] && [ -n "${join_line}" ] && [ "${inst_line}" -lt "${join_line}" ]; } \
  || fail_case "case 11: the OIDC CA is not installed before kubeadm join"
code "${DIST}" | grep 'place "${ip}" oidc-ca.crt /tmp/oidc-ca.crt' >/dev/null \
  || fail_case "case 11: distribute-join-kakao.sh does not stage the OIDC CA"
code "${DIST}" | grep -A2 'references oidc-ca-file but /etc/kubernetes/pki/oidc-ca.crt on master-1 is missing' | grep 'exit 1' >/dev/null \
  || fail_case "case 11: distribute-join-kakao.sh does not refuse without the OIDC CA"
code "${WROOT}/scripts/test/verify-cluster.sh" | grep 'kubeadm.k8s.io/v1beta3' >/dev/null \
  || fail_case "case 11: verify-cluster.sh gate does not handle the v1beta3 map form"
ok "11 later-joined masters: CA fetched and installed before join, join refused without it (Vagrant and Kakao)"

echo "PASS: R297 kubeadm-config OIDC harness, ${PASS} checks"
