#!/usr/bin/env bash
# R268: nfs-root-squash-align.sh target resolution and guards, run as a dry run against fake kubectl
# output and a temp share (no root needed). Asserts: static SeaweedFS map, pod-derived uid:gid,
# unresolved PV left alone, a Running different-uid consumer refused, /etc/exports never read for writing.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin" "${tmp}/share/storage/data-filer-seaweedfs-filer-0" "${tmp}/share/database/db-1" \
  "${tmp}/share/monitoring/unknown-pvc" "${tmp}/share/monitoring/clash"

printf '#!/bin/sh\nexec "$@"\n' >"${tmp}/bin/sudo"
printf '#!/bin/sh\nexit 3\n' >"${tmp}/bin/systemctl"
cat >"${tmp}/bin/kubectl" <<'KEOF'
#!/bin/sh
case "$*" in
  *csidriver*) echo "ReadWriteOnceWithFSType" ;;
  *"get pv"*) cat "$FIXTURE_DIR/pv.json" ;;
  *"get pods"*) cat "$FIXTURE_DIR/pods.json" ;;
esac
KEOF
# BSD stat (macOS dev hosts) has no -c; translate the two formats the script uses.
if ! stat -c '%a' "${tmp}" >/dev/null 2>&1; then
  cat >"${tmp}/bin/stat" <<'SEOF'
#!/bin/sh
fmt="$2"; shift 2
case "$fmt" in
  %U:%G) /usr/bin/stat -f '%Su:%Sg' "$@" ;;
  %a) /usr/bin/stat -f '%Lp' "$@" ;;
esac
SEOF
fi
chmod +x "${tmp}/bin/"*

pv() { printf '{"spec":{"csi":{"driver":"nfs.csi.k8s.io","volumeAttributes":{"subdir":"%s"}},"claimRef":{"namespace":"%s","name":"%s"}}}' "$1" "$2" "$3"; }
{
  echo '{"items":['
  pv storage/data-filer-seaweedfs-filer-0 storage data-filer-seaweedfs-filer-0; echo ,
  pv database/db-1 database db-1; echo ,
  pv monitoring/unknown-pvc monitoring unknown-pvc; echo ,
  pv monitoring/clash monitoring clash
  echo ']}'
} >"${tmp}/pv.json"
pod() { # ns claim name phase containerUser fsGroup
  printf '{"metadata":{"namespace":"%s","name":"%s"},"status":{"phase":"%s"},"spec":{"securityContext":{"fsGroup":%s},"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"%s"}}],"containers":[{"name":"c","securityContext":{"runAsUser":%s},"volumeMounts":[{"name":"v"}]}]}}' \
    "$1" "$3" "$4" "$6" "$2" "$5"
}
# clash: two consumers; the Running one runs as a uid other than the derived target.
{
  echo '{"items":['
  pod database db-1 db-pod Running 26 26; echo ,
  pod monitoring clash clash-a Pending 999 999; echo ,
  pod monitoring clash clash-b Running 1234 999
  echo ']}'
} >"${tmp}/pods.json"

out="$(PATH="${tmp}/bin:${PATH}" FIXTURE_DIR="${tmp}" NFS_SHARE_PATH="${tmp}/share" \
  bash "${root}/scripts/cluster/nfs-root-squash-align.sh" 2>&1)" && rc=0 || rc=$?

fail() { echo "FAIL: $1" >&2; echo "${out}" >&2; exit 1; }
grep -Fq "target 1000:1000 (static map)" <<<"${out}" || fail "seaweedfs static target"
grep -Fq "target 26:26 (from pod securityContext)" <<<"${out}" || fail "pod-derived target"
grep -Fq "UNRESOLVED" <<<"${out}" || fail "unresolved PV must be reported"
grep -Fq "REFUSE: a Running consumer runs as a different non-root uid: clash-b uid=1234" <<<"${out}" || fail "different-uid guard"
grep -Fq "PV permission timer: MISSING" <<<"${out}" || fail "timer precondition"
grep -Fq "(DRY-RUN)" <<<"${out}" || fail "default mode must be dry run"
[ "${rc}" -eq 3 ] || fail "skipped PVs must exit 3 (got ${rc})"
grep -Fq "chmod 750 ${tmp}/share" <<<"${out}" || fail "share root hardening planned"
[ "$(PATH="${tmp}/bin:${PATH}" stat -c '%a' "${tmp}/share")" != "750" ] || fail "dry run must not chmod"
