#!/bin/bash
# R275: ensure_s3_identities / s3_sync_server_config hardening (D-s3ops-5, #415) against a fake kubectl.
# Run from the repo root. Covers: numeric and leading-zero keys stay YAML strings, the key-bearing
# temp file is removed on success / apply failure / TERM, the caller's traps and
# S3_IDENTITIES_CHANGED survive, s3_sync_server_config patch counts and patch-failure propagation.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
export ST="$k/store" TMPDIR="$k/tmp"; mkdir -p "$ST" "$TMPDIR"
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
case "$1" in
  get)
    if [ "$2" = deployment ]; then
      [ -f "${ST}/deploy_exists" ] || { echo 'Error from server (NotFound): deployments.apps "seaweedfs-s3" not found' >&2; exit 1; }
      [ -f "${ST}/deploy_sha" ] && cat "${ST}/deploy_sha"; exit 0
    fi
    name="$3"; ns="$5"
    if [ "${7:-}" = name ]; then
      [ -f "${ST}/${ns}__${name}__exists" ] && { echo "secret/${name}"; exit 0; }
      echo "Error from server (NotFound): secrets \"${name}\" not found" >&2; exit 1
    fi
    key="${7#jsonpath=\{.data.}"; key="${key%\}}"
    [ -f "${ST}/${ns}__${name}__${key}" ] && cat "${ST}/${ns}__${name}__${key}"; exit 0 ;;
  patch)
    [ -z "${FAKE_PATCH_FAIL:-}" ] || { echo "patch failed" >&2; exit 1; }
    [[ "$*" =~ narwhal.io/s3-config-sha256\":\"([0-9a-f]+)\" ]] || exit 1
    printf '%s' "${BASH_REMATCH[1]}" > "${ST}/deploy_sha"; echo patch >> "${ST}/patches" ;;
  create) touch "${ST}/ns_done"; printf 'kind: Namespace\n' ;;
  apply)
    [ "$3" != "-" ] || { cat >/dev/null; exit 0; }
    [ ! -d "$3" ] || { echo dir >> "${ST}/dir_applies"; exit 0; }   # an apply of the unwritten server config
    ns=$(awk '/^  namespace:/{print $2}' "$3"); name=$(awk '/^  name:/{print $2; exit}' "$3")
    echo "$3" >> "${ST}/tmpfiles"
    if [ "${FAKE_APPLY_TERM:-}" = "$name" ]; then kill -TERM "$PPID"; sleep 1; fi
    [ "${FAKE_APPLY_FAIL:-}" = "$name" ] && { echo "apply failed" >&2; exit 1; }
    touch "${ST}/${ns}__${name}__exists"; cp "$3" "${ST}/${ns}__${name}__RAW"
    # store each `  key: value` pair base64 encoded, like a Secret read back through jsonpath
    python3 - "$3" "${ST}/${ns}__${name}__" <<'PY'
import base64, sys, yaml
for key, val in (yaml.safe_load(open(sys.argv[1])).get("stringData") or {}).items():
    open(sys.argv[2] + key, "w").write(base64.b64encode(str(val).encode()).decode())
PY
    # the server config is written last into the same temp path: replace it by a directory so that write fails
    if [ "${FAKE_APPLY_MKDIR:-}" = "$name" ]; then rm -f "$3"; mkdir "$3"; fi
    ;;
esac
FAKE
# BSD/macOS mktemp ignores TMPDIR when called without a template; pin it so leaks are observable
real_mktemp=$(command -v mktemp)
mkdir -p "$k/wdir"
cat > "$k/mktemp" <<SHIM
#!/bin/bash
# after the namespaces are created (= the temp file of the apply section), FAKE_MKTEMP_DIR=1 hands
# out a directory so the write to it fails
[ "\${FAKE_MKTEMP_DIR:-}" = 1 ] && [ -f "\${ST}/ns_done" ] && { echo "$k/wdir"; exit 0; }
[ \$# -eq 0 ] && exec $real_mktemp "\${TMPDIR}/tmp.XXXXXXXXXX"
exec $real_mktemp "\$@"
SHIM
chmod +x "$k/kubectl" "$k/mktemp"
export PATH="$k:$PATH"
# shellcheck source=/dev/null
source scripts/common/lib.sh

fail() { echo "FAIL: $*" >&2; exit 1; }
seed() { printf '%s' "$4" | base64 | tr -d '\n' > "$ST/$1__$2__$3"; touch "$ST/$1__$2__exists"; }
reset() { rm -rf "$ST"; mkdir -p "$ST"; }
leaked() { find "$TMPDIR" -type f | grep -q .; }
run() { local rc=0; ensure_s3_identities >"$k/out" 2>&1 || rc=$?; return "$rc"; }

# --- numeric and leading-zero keys must round-trip as YAML strings (an unquoted 0123... is an int)
NUM_AK=12345678901234567890; NUM_SK=012345678901234567890123456789012345678901234567
seed monitoring loki-s3-credentials S3_ACCESS_KEY_ID "$NUM_AK"
seed monitoring loki-s3-credentials S3_SECRET_ACCESS_KEY "$NUM_SK"
run || fail "numeric-key run failed"
python3 - "$ST" "$NUM_AK" "$NUM_SK" <<'PY'
import sys, yaml
st, ak, sk = sys.argv[1:4]
d = yaml.safe_load(open(f"{st}/monitoring__loki-s3-credentials__RAW"))["stringData"]
assert d["S3_ACCESS_KEY_ID"] == ak and d["S3_SECRET_ACCESS_KEY"] == sk, "numeric keys must stay strings"
PY
leaked && fail "temp file leaked after a successful run"
[ "$S3_IDENTITIES_CHANGED" = true ] || fail "S3_IDENTITIES_CHANGED must propagate out of the apply section"

# --- temp file removal on apply failure and on TERM
reset; FAKE_APPLY_FAIL=cnpg-s3-credentials run && fail "client apply failure must fail the run"
leaked && fail "temp file leaked after a client apply failure"
reset; FAKE_APPLY_FAIL=seaweedfs-s3-config run && fail "server Secret apply failure must fail the run"
leaked && fail "temp file leaked after a server Secret apply failure"
reset; FAKE_APPLY_TERM=velero-s3-credentials run && fail "TERM during apply must fail the run"
leaked && fail "temp file leaked after TERM"
[ -s "$ST/tmpfiles" ] || fail "fixture never wrote a temp file (test is vacuous)"

# --- LOW 1: a failing write of the temp file (here: the path is a directory) fails the run, applies nothing
reset; FAKE_MKTEMP_DIR=1 run && fail "a failing temp-file write must fail the run"
[ -e "$ST/ns_done" ] || fail "fixture never reached the apply section (test is vacuous)"
[ ! -e "$ST/tmpfiles" ] && [ ! -e "$ST/applies" ] && [ ! -e "$ST/dir_applies" ] || fail "nothing may be applied after a failed temp-file write"
# same for the final write (the server config), after all four client Secrets were applied
reset; FAKE_APPLY_MKDIR=cnpg-s3-credentials run && fail "a failing write of the server config must fail the run"
[ -e "$ST/database__cnpg-s3-credentials__exists" ] || fail "fixture did not reach the last client (test is vacuous)"
[ ! -e "$ST/dir_applies" ] || fail "server config must not be applied after a failed write"
rm -rf "${TMPDIR:?}"/*

# --- the caller's own EXIT trap is untouched: it runs once, after the function returned
reset
m="$k/marker"; : > "$m"
bash -c 'source scripts/common/lib.sh; trap "echo caller >> \"$1\"" EXIT; ensure_s3_identities >/dev/null 2>&1; echo after >> "$1"' _ "$m" || fail "wrapped run failed"
[ "$(tr '\n' ' ' < "$m")" = "after caller " ] || fail "caller trap changed: $(tr '\n' ' ' < "$m")"

# --- s3_sync_server_config: no Deployment = no-op, stale = one patch, same = none, patch failure propagates
reset; run || fail "seed run failed"
cfg_sha() { python3 - "$ST" <<'PY'
import base64, hashlib, sys
print(hashlib.sha256(base64.b64decode(open(f"{sys.argv[1]}/storage__seaweedfs-s3-config__seaweedfs_s3_config").read())).hexdigest())
PY
}
npatch() { if [ -f "$ST/patches" ]; then wc -l < "$ST/patches" | tr -d ' '; else echo 0; fi; }
s3_sync_server_config 2>/dev/null || fail "sync without a Deployment must be a no-op"
[ "$(npatch)" = 0 ] || fail "no Deployment must not patch"
touch "$ST/deploy_exists"; echo stale > "$ST/deploy_sha"
s3_sync_server_config 2>/dev/null || fail "stale sync failed"
[ "$(npatch)" = 1 ] && [ "$(<"$ST/deploy_sha")" = "$(cfg_sha)" ] || fail "stale checksum must give exactly one patch"
s3_sync_server_config 2>/dev/null || fail "same-checksum sync failed"
[ "$(npatch)" = 1 ] || fail "same checksum must give zero patches"
echo stale > "$ST/deploy_sha"
FAKE_PATCH_FAIL=1 s3_sync_server_config 2>/dev/null && fail "patch failure must propagate"
echo "OK"
