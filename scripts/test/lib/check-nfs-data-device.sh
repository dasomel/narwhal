#!/usr/bin/env bash
# Behavioral check for the NFS_DATA_DEVICE block of 01-nfs-server.sh (R264): extract the real block
# and run it against fake blkid/findmnt/mkfs.xfs/sudo. Blank device -> mkfs; existing xfs -> no mkfs;
# other fs -> fail; final mount without prjquota -> fail; non-empty share path -> refuse (never mkfs, never mount).
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin" "${tmp}/etc"
: > "${tmp}/etc/fstab"

# sudo runs the command with /etc/ redirected into the sandbox.
cat > "${tmp}/bin/sudo" <<'F'
#!/usr/bin/env bash
args=()
for a in "$@"; do args+=("${a/#\/etc\//${FAKE_ROOT}/etc/}"); done
exec "${args[@]}"
F
cat > "${tmp}/bin/blkid" <<'F'
#!/usr/bin/env bash
# -p (low-level probe) must be passed: without it blkid can serve a stale cache entry.
case " $* " in
  *" -p "*) ;;
  *) echo "blkid called without -p: $*" >&2; exit 2 ;;
esac
case "$*" in
  *"-s TYPE"*) printf '%s' "${FAKE_FS:-}" ;;
  *"-s UUID"*) echo "uuid-1234" ;;
esac
F
cat > "${tmp}/bin/findmnt" <<'F'
#!/usr/bin/env bash
case "$*" in
  *SOURCE*) printf '%s' "${FAKE_MNT_SRC:-}" ;;
  *FSTYPE*) echo "${FAKE_FSTYPE:-xfs} ${FAKE_OPTS:-rw,prjquota}" ;;
esac
F
cat > "${tmp}/bin/mkfs.xfs" <<'F'
#!/usr/bin/env bash
echo "mkfs.xfs $*" >> "${FAKE_LOG}"
F
for c in mount systemctl; do
  printf '#!/usr/bin/env bash\necho "%s $*" >> "$FAKE_LOG"\n' "${c}" > "${tmp}/bin/${c}"
done
chmod +x "${tmp}"/bin/*

blk=$(sed -n '/^if \[ -n "\${NFS_DATA_DEVICE}" \]; then/,/^fi$/p' "${root}/scripts/cluster/01-nfs-server.sh")
[ -n "${blk}" ] || { echo "NFS_DATA_DEVICE block not found" >&2; exit 1; }
# a regular file stands in for the block device
blk=$(sed 's/\[ -b /[ -e /; s/\[ ! -b /[ ! -e /' <<<"${blk}")

export PATH="${tmp}/bin:${PATH}" FAKE_ROOT="${tmp}" FAKE_LOG="${tmp}/log"
export NFS_DATA_DEVICE="${tmp}/dev" NFS_SHARE_PATH="${tmp}/share"
: > "${NFS_DATA_DEVICE}"

run() { # run <fs> -> rc
  : > "${FAKE_LOG}"
  ( export FAKE_FS="$1"; set -euo pipefail; eval "${blk}" ) >"${tmp}/out" 2>&1
}

mkdir -p "${NFS_SHARE_PATH}"
run "" || { cat "${tmp}/out" >&2; echo "blank device must succeed" >&2; exit 1; }
grep -Fq "mkfs.xfs" "${FAKE_LOG}" || { echo "blank device must be formatted" >&2; exit 1; }
grep -Fq "prjquota" "${tmp}/etc/fstab" || { echo "fstab must carry prjquota" >&2; exit 1; }

run xfs || { echo "existing xfs must succeed" >&2; exit 1; }
! grep -Fq "mkfs.xfs" "${FAKE_LOG}" || { echo "existing xfs must never be formatted" >&2; exit 1; }

if FAKE_OPTS="rw,noquota" run xfs; then echo "xfs mount without prjquota must fail" >&2; exit 1; fi
grep -Fq "not an XFS mount with prjquota" "${tmp}/out" || { echo "missing prjquota must hit the final check" >&2; exit 1; }
if FAKE_FSTYPE=ext4 run xfs; then echo "non-xfs mount must fail" >&2; exit 1; fi

if run ext4; then echo "ext4 device must fail" >&2; exit 1; fi
! grep -Fq "mkfs.xfs" "${FAKE_LOG}" || { echo "ext4 must never be formatted" >&2; exit 1; }

touch "${NFS_SHARE_PATH}/live-data"
if run ""; then echo "non-empty share path must be refused" >&2; exit 1; fi
! grep -Eq "mkfs.xfs|^mount" "${FAKE_LOG}" || { echo "non-empty share path: no mkfs/mount allowed" >&2; exit 1; }
echo ok
