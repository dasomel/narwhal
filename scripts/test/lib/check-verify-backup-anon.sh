#!/bin/bash
# R271 (behaviour): the anonymous S3 check of scripts/test/verify-backup.sh (D-s3ops-2, #419) runs python3 in a
# Ready narwhal-db pod (container postgres). The fake kubectl executes the real embedded python
# locally with urllib.request.urlopen stubbed per FAKE_HTTP, so the HTTPError handling is exercised.
# Run from the repo root. Only the S3 verdict line is asserted.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
mkdir -p "$k/py"
cat > "$k/py/sitecustomize.py" <<'PY'
import os, urllib.error, urllib.request

def fake_urlopen(url, timeout=None):
    mode = os.environ["FAKE_HTTP"]
    if mode == "200":
        class R: status = 200
        return R()
    if mode == "ERR":
        raise urllib.error.URLError("connection reset")
    raise urllib.error.HTTPError(url, int(mode), "x", {}, None)

urllib.request.urlopen = fake_urlopen
PY
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
# get pod -n database -l <selector> -o jsonpath=... | exec -n database <pod> -c postgres -i -- python3 - <url>
echo "$*" >> "${CALLS}"
case "$1" in
  get)
    [ "$2" = pod ] && [ "$4" = database ] || exit 0
    case "$6" in
      *instanceRole=primary*) [ "${POD_PRIMARY:-}" = none ] || echo "narwhal-db-1 True" ;;
      *) [ -z "${POD_ANY:-}" ] || printf 'narwhal-db-2 False\nnarwhal-db-3 True\n' ;;
    esac ;;
  exec)
    [ "${EXEC_FAIL:-}" != 1 ] || { echo 'exec: "python3": executable file not found' >&2; exit 126; }
    [ "$3" = database ] && [ "$6" = postgres ] || exit 1
    shift 8; PYTHONPATH="$K_PY" "$@" ;;
esac
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH" CALLS="$k/calls" K_PY="$k/py"

fail() { echo "FAIL: $*" >&2; exit 1; }
verdict() { : > "$CALLS"; bash scripts/test/verify-backup.sh --section=seaweedfs 2>/dev/null | grep -E 'S3 (endpoint|anonymous)' || true; }
expect() { # <label> <regex>
  local out; out=$(verdict)
  [[ "$out" =~ $2 ]] || fail "$1: got '${out}'"
}

export POD_PRIMARY=ok FAKE_HTTP=403
expect "403 from the primary" '\[PASS\].*enforces authentication.*403'
grep -Fq "exec -n database narwhal-db-1 -c postgres -i" "$CALLS" || fail "must exec python3 in the narwhal-db primary, postgres container"
! grep -E 'exec.*(velero|wget)' "$CALLS" || fail "must not use a Velero pod or wget"
FAKE_HTTP=200 expect "200 = auth open" '\[FAIL\].*HTTP 200.*not enforced'
for s in 404 500 ERR; do FAKE_HTTP=$s expect "$s" "\[FAIL\].*unhealthy: HTTP $s"; done
POD_PRIMARY=none POD_ANY=1 expect "falls back to a Ready non-primary pod" '\[PASS\].*403'
grep -Fq "exec -n database narwhal-db-3 -c postgres" "$CALLS" || fail "fallback must pick the Ready pod, not narwhal-db-2"
POD_PRIMARY=none POD_ANY='' expect "no Ready pod" '\[FAIL\].*no Ready narwhal-db pod'
EXEC_FAIL=1 expect "exec failure (python3 absent)" '\[FAIL\].*could not run'
EXEC_FAIL=1 FAKE_HTTP=403 bash scripts/test/verify-backup.sh --section=seaweedfs 2>/dev/null | grep -q 'PASS.*enforces authentication' && fail "failed exec must never PASS"
echo "OK"
