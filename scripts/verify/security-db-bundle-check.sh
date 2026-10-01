#!/usr/bin/env bash
set -euo pipefail

# Verify an offline security DB bundle and its provenance manifest.
# ASSUMPTION: the extended manifest uses source, fetched_at (UTC RFC3339),
# scanner {name, version}, schema_version, artifacts [{path, sha256}], and
# osv_reviewed. fetch-security-db.sh currently emits only a subset of this
# contract; its output therefore needs a later producer update before passing.
# ASSUMPTION: the repo's Trivy 0.60.0 pin supports security DB schema 2.

ROOT=""; MAX_AGE_DAYS=7; NOW=""; SELF_TEST=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) [ "$#" -ge 2 ] || { echo 'FAIL: --root requires DIR'; exit 2; }; ROOT=$2; shift 2 ;;
    --max-age-days) [ "$#" -ge 2 ] || { echo 'FAIL: --max-age-days requires N'; exit 2; }; MAX_AGE_DAYS=$2; shift 2 ;;
    --now) [ "$#" -ge 2 ] || { echo 'FAIL: --now requires UTC timestamp'; exit 2; }; NOW=$2; shift 2 ;;
    --self-test) SELF_TEST=1; shift ;;
    *) echo "usage: $0 --root DIR [--max-age-days N] [--now UTC] | --self-test"; exit 2 ;;
  esac
done

verify() {
  python3 - "$1" "$MAX_AGE_DAYS" "$NOW" <<'PY'
import datetime, hashlib, json, pathlib, re, sys

root = pathlib.Path(sys.argv[1])
max_age = int(sys.argv[2])
now_arg = sys.argv[3]
manifest_path = root / "manifest.json"
errors = []
try:
    if root.is_symlink() or not root.is_dir(): raise ValueError("bundle root missing or symlink")
    if manifest_path.is_symlink() or not manifest_path.is_file(): raise ValueError("manifest missing or symlink")
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict): raise ValueError("manifest must be a JSON object")
    for key in ("source", "fetched_at", "scanner", "schema_version", "artifacts"):
        if key not in doc: errors.append("missing provenance/manifest field: " + key)
    if not isinstance(doc.get("source"), str) or not doc.get("source", "").strip(): errors.append("source must be non-empty")
    scanner = doc.get("scanner")
    if not isinstance(scanner, dict) or not scanner.get("name") or not scanner.get("version"):
        errors.append("scanner name and version are required")
    elif scanner["name"].lower() != "trivy" or scanner["version"] != "0.60.0":
        errors.append("unsupported scanner/version; repo pin is Trivy 0.60.0")
    if doc.get("schema_version") != 2: errors.append("unsupported schema_version for pinned Trivy 0.60.0 (expected 2)")
    try:
        fetched = datetime.datetime.fromisoformat(doc["fetched_at"].replace("Z", "+00:00"))
        now = datetime.datetime.fromisoformat(now_arg.replace("Z", "+00:00")) if now_arg else datetime.datetime.now(datetime.timezone.utc)
        if fetched.tzinfo is None or now.tzinfo is None: raise ValueError("timezone required")
        age = (now - fetched).total_seconds()
        if age < 0 or age > max_age * 86400: errors.append("bundle is future-dated or stale")
    except (KeyError, TypeError, ValueError): errors.append("fetched_at/--now must be timezone-aware ISO-8601")
    artifacts = doc.get("artifacts")
    listed = set()
    if not isinstance(artifacts, list) or not artifacts: errors.append("artifacts must be a non-empty list"); artifacts=[]
    for item in artifacts:
        if not isinstance(item, dict): errors.append("artifact entry must be an object"); continue
        name, digest = item.get("path"), item.get("sha256")
        if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name or name == "manifest.json":
            errors.append("artifact path must be a safe basename"); continue
        if name in listed: errors.append("duplicate artifact path: " + name)
        listed.add(name)
        path = root / name
        if path.is_symlink() or not path.is_file(): errors.append("missing, symlink, or non-regular artifact: " + name); continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest): errors.append("invalid sha256 for " + name); continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest: errors.append("SHA-256 mismatch: " + name)
    actual = set()
    for path in root.iterdir():
        if path.name == "manifest.json": continue
        actual.add(path.name)
        if path.is_symlink() or not path.is_file(): errors.append("symlink or non-regular bundle entry: " + path.name)
    if actual != listed: errors.append("unlisted/missing files: " + repr(sorted(actual ^ listed)))
    source = str(doc.get("source", "")).lower()
    if "osv" in source and doc.get("osv_reviewed") is not True: errors.append("OSV source requires osv_reviewed: true")
except (OSError, json.JSONDecodeError, ValueError) as exc:
    errors.append("manifest/bundle parse error: " + str(exc))
print("Evaluated: artifacts=%d, directory_entries=%d, failures=%d" % (len(doc.get("artifacts", [])) if "doc" in locals() and isinstance(doc, dict) and isinstance(doc.get("artifacts"), list) else 0, len(list(root.iterdir())) if root.exists() and root.is_dir() else 0, len(errors)))
for error in errors: print("FAIL: " + error, file=sys.stderr)
if errors: raise SystemExit(1)
print("PASS: security DB bundle verified")
PY
}

if [ "$SELF_TEST" -eq 1 ]; then
  NOW="2026-09-30T01:00:00Z"
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  make_good() {
    local dir="$tmp/$1"; mkdir -p "$dir"; printf 'db data\n' > "$dir/db.bin"
    python3 - "$dir" "$2" <<'PY'
import datetime,hashlib,json,pathlib,sys
d=pathlib.Path(sys.argv[1]); mode=sys.argv[2]
m={"source":"trivy-db","fetched_at":"2026-09-30T00:00:00Z","scanner":{"name":"Trivy","version":"0.60.0"},"schema_version":2,"artifacts":[{"path":"db.bin","sha256":hashlib.sha256((d/"db.bin").read_bytes()).hexdigest()}]}
if mode != "no-osv": m["osv_reviewed"]=True
if mode == "missing-field": del m["source"]
if mode == "bad-schema": m["schema_version"]=99
if mode == "osv": m["source"]="osv-offline-db"; m.pop("osv_reviewed",None)
if mode == "stale": m["fetched_at"]="2026-01-01T00:00:00Z"
(d/"manifest.json").write_text(json.dumps(m))
PY
  }
  make_good good ok
  verify "$tmp/good" >/dev/null && echo 'PASS: good fixture' || { echo 'FAIL: good fixture'; exit 1; }
  for scenario in missing-manifest tampered missing-field bad-schema stale extra osv symlink; do
    make_good "$scenario" no-osv
    case "$scenario" in
      missing-manifest) rm "$tmp/$scenario/manifest.json" ;;
      tampered) printf 'changed\n' > "$tmp/$scenario/db.bin" ;;
      missing-field) make_good "$scenario" missing-field ;;
      bad-schema) make_good "$scenario" bad-schema ;;
      stale) make_good "$scenario" stale ;;
      extra) printf 'extra\n' > "$tmp/$scenario/extra.bin" ;;
      osv) make_good "$scenario" osv ;;
      symlink) rm "$tmp/$scenario/db.bin"; ln -s /dev/null "$tmp/$scenario/db.bin" ;;
    esac
    if verify "$tmp/$scenario" >/dev/null 2>&1; then echo "FAIL: $scenario accepted"; exit 1; else echo "PASS: $scenario rejected"; fi
  done
  echo 'PASS: all security DB bundle self-tests'; exit 0
fi
[ -n "$ROOT" ] || { echo 'FAIL: --root DIR is required'; exit 2; }
verify "$ROOT"
