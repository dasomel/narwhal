#!/bin/bash
# R250: behaviour of ensure_s3_identities (scripts/common/lib.sh) against a stateful fake kubectl.
# Run from the repo root. Key values are asserted, never printed.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
export ST="$k/store"; mkdir -p "$ST"
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
# get secret <name> -n <ns> -o jsonpath={.data.<key>} | create namespace <n> ... | apply -f <file|->
case "$1" in
  get)
    name="$3"; ns="$5"; key="${7#jsonpath=\{.data.}"; key="${key%\}}"
    if [ "${FAKE_FAIL:-}" = apierror ]; then echo "Unable to connect to the server: net/http: TLS handshake timeout" >&2; exit 1; fi
    if [ "${FAKE_FAIL_KEY:-}" = "$key" ]; then echo "etcdserver: request timed out" >&2; exit 1; fi
    f="${ST}/${ns}__${name}__${key}"
    [ -f "$f" ] && { cat "$f"; exit 0; }
    if [ -f "${ST}/${ns}__${name}__exists" ]; then exit 0; fi
    echo "Error from server (NotFound): secrets \"${name}\" not found" >&2; exit 1 ;;
  create) printf 'kind: Namespace\nmetadata:\n  name: %s\n' "$3" ;;
  apply)
    src="$3"; if [ "$src" = "-" ]; then cat >/dev/null; exit 0; fi
    ns=$(awk '/^  namespace:/{print $2}' "$src"); name=$(awk '/^  name:/{print $2; exit}' "$src")
    touch "${ST}/${ns}__${name}__exists"; echo "$ns/$name" >> "${ST}/applies"
    cp "$src" "${ST}/${ns}__${name}__RAW"
    while IFS= read -r line; do
      if [[ "$line" =~ ^\ \ ([A-Za-z0-9_-]+):\ (.+)$ ]] && [ "${BASH_REMATCH[2]}" != "|" ]; then
        v="${BASH_REMATCH[2]}"; v="${v#\'}"; v="${v%\'}"
        printf '%s' "$v" | base64 | tr -d '\n' > "${ST}/${ns}__${name}__${BASH_REMATCH[1]}"
      fi
    done < <(sed -n '/^stringData:/,$p' "$src" | tail -n +2)
    ;;
esac
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH"
# shellcheck source=/dev/null
source scripts/common/lib.sh

fail() { echo "FAIL: $*" >&2; exit 1; }
field() { base64 -d < "$ST/$1__$2__$3"; }
seed() { printf '%s' "$4" | base64 | tr -d '\n' > "$ST/$1__$2__$3"; touch "$ST/$1__$2__exists"; }
run() { local rc=0; ensure_s3_identities >"$k/out" 2>&1 || rc=$?; OUT=$(<"$k/out"); return "$rc"; }

# --- fresh install: 4 client Secrets + server config, random, distinct, bucket-scoped
run || fail "fresh run failed"
[ "$S3_IDENTITIES_CHANGED" = true ] || fail "fresh run must report a change"
python3 - "$ST" <<'PY'
import base64, json, os, re, sys
st = sys.argv[1]
def f(ns, name, key): return base64.b64decode(open(f"{st}/{ns}__{name}__{key}").read()).decode()
cl = {
 "loki": ("monitoring","loki-s3-credentials","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY","loki"),
 "tempo": ("monitoring","tempo-s3-credentials","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY","tempo"),
 "velero": ("storage","velero-s3-credentials","access-key","secret-key","velero"),
 "cnpg": ("database","cnpg-s3-credentials","ACCESS_KEY_ID","ACCESS_SECRET_KEY","cnpg-backup"),
}
cfg = json.loads(f("storage","seaweedfs-s3-config","seaweedfs_s3_config"))
ids = {i["name"]: i for i in cfg["identities"]}
assert set(ids) == set(cl), ids.keys()
seen = set()
for n,(ns,sec,af,sf,b) in cl.items():
    ak, sk = f(ns,sec,af), f(ns,sec,sf)
    assert re.fullmatch(r"[0-9A-F]{20}", ak) and re.fullmatch(r"[0-9a-f]{48}", sk), n
    assert "admin" not in (ak, sk) and ak not in seen and sk not in seen; seen |= {ak, sk}
    c = ids[n]["credentials"][0]; assert (c["accessKey"], c["secretKey"]) == (ak, sk), n
    # cross-bucket deny: every action is scoped to this client's own bucket, none is global/Admin
    assert ids[n]["actions"] and all(a.split(":")[1:] == [b] and a.split(":")[0] != "Admin" for a in ids[n]["actions"]), (n, ids[n]["actions"])
cloud = open(f"{st}/storage__velero-s3-credentials__RAW").read()
ak, sk = f("storage","velero-s3-credentials","access-key"), f("storage","velero-s3-credentials","secret-key")
assert f"aws_access_key_id = {ak}" in cloud and f"aws_secret_access_key = {sk}" in cloud
PY
# no value reaches the log
for s in loki:monitoring:loki-s3-credentials:S3_SECRET_ACCESS_KEY cnpg:database:cnpg-s3-credentials:ACCESS_SECRET_KEY; do
  IFS=: read -r _ ns sec key <<<"$s"
  v=$(field "$ns" "$sec" "$key"); case "$OUT" in *"$v"*) fail "log leaks a key" ;; esac
done

# --- rerun is a no-op: existing random keys are kept
before=$(cat "$ST"/*__S3_*  "$ST"/*__ACCESS_* "$ST"/*__access-key "$ST"/*__secret-key | sha256sum)
run || fail "rerun failed"
[ "$S3_IDENTITIES_CHANGED" = false ] || fail "rerun must not report a change"
after=$(cat "$ST"/*__S3_* "$ST"/*__ACCESS_* "$ST"/*__access-key "$ST"/*__secret-key | sha256sum)
[ "$before" = "$after" ] || fail "rerun changed existing keys"

# --- default admin/admin pair (cnpg + velero) is replaced exactly once, the others are kept
loki_before=$(field monitoring loki-s3-credentials S3_SECRET_ACCESS_KEY)
seed database cnpg-s3-credentials ACCESS_KEY_ID admin; seed database cnpg-s3-credentials ACCESS_SECRET_KEY admin
seed storage velero-s3-credentials access-key admin;   seed storage velero-s3-credentials secret-key admin
run || fail "default replacement failed"
[ "$S3_IDENTITIES_CHANGED" = true ] || fail "default replacement must report a change"
case "$OUT" in *"replacing the default"*) ;; *) fail "replacement not logged" ;; esac
[ "$(field database cnpg-s3-credentials ACCESS_SECRET_KEY)" != admin ] || fail "cnpg default kept"
[ "$(field storage velero-s3-credentials secret-key)" != admin ] || fail "velero default kept"
[ "$(field monitoring loki-s3-credentials S3_SECRET_ACCESS_KEY)" = "$loki_before" ] || fail "loki key was regenerated"
cnpg_new=$(field database cnpg-s3-credentials ACCESS_SECRET_KEY)
run || fail "second run failed"
[ "$S3_IDENTITIES_CHANGED" = false ] && [ "$(field database cnpg-s3-credentials ACCESS_SECRET_KEY)" = "$cnpg_new" ] || fail "replacement must happen exactly once"

# --- fail closed: nothing is written on partial/empty/odd state or a read error
expect_fail() {
  : > "$ST/applies"; if run; then fail "$1 must fail"; fi
  [ ! -s "$ST/applies" ] || fail "$1 must not write anything"
}
seed database cnpg-s3-credentials ACCESS_SECRET_KEY ""; expect_fail "empty secret half"
seed database cnpg-s3-credentials ACCESS_SECRET_KEY admin; expect_fail "one default half"
seed database cnpg-s3-credentials ACCESS_SECRET_KEY 'bad"quote'; expect_fail "unsupported characters"
seed database cnpg-s3-credentials ACCESS_SECRET_KEY "$cnpg_new"
FAKE_FAIL=apierror expect_fail "kubectl API error"
FAKE_FAIL_KEY=ACCESS_KEY_ID expect_fail "read error on the access key only"
FAKE_FAIL_KEY=secret-key expect_fail "read error on the secret key only"
run || fail "recovery after fixing state failed"
echo "OK"
