#!/usr/bin/env bash
set -euo pipefail

# Verify every kubelet static-pod input against a protected SHA-256 inventory.
# Expected file format: one '<64 lowercase hex>  <basename>' line per regular file.
# Blank lines and comments are ignored. No directories or symlinks are permitted.
# D1: Production ownership is fixed at root:root; self-test passes a stat-derived
# fixture expectation when the caller cannot chown. This keeps fixture checks useful
# without weakening the node default. Escape hatch: run as root for production checks.

ROOT=/
EXPECTED=
SELF_TEST=0
OWNER_EXPECTATION=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) [ "$#" -ge 2 ] || { echo "FAIL: --root requires DIR"; exit 2; }; ROOT=$2; shift 2 ;;
    --expected) [ "$#" -ge 2 ] || { echo "FAIL: --expected requires FILE"; exit 2; }; EXPECTED=$2; shift 2 ;;
    --self-test) SELF_TEST=1; shift ;;
    --owner-expectation) [ "$#" -ge 2 ] || { echo "FAIL: --owner-expectation requires FILE"; exit 2; }; OWNER_EXPECTATION=$2; shift 2 ;;
    *) echo "usage: $0 [--root DIR] --expected FILE | --self-test"; exit 2 ;;
  esac
done

MANIFEST_DIR="${ROOT%/}/etc/kubernetes/manifests"
[ "$ROOT" != / ] || MANIFEST_DIR=/etc/kubernetes/manifests

file_hash() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
file_details() {
  if stat -c '%a %u:%g' "$1" >/dev/null 2>&1; then stat -c '%a %u:%g' "$1"
  else stat -f '%Lp %u:%g' "$1"; fi
}
file_owner() {
  if stat -c '%u:%g' "$1" >/dev/null 2>&1; then stat -c '%u:%g' "$1"
  else stat -f '%u:%g' "$1"; fi
}
expected_contains_name() {
  local wanted="$1" expected_file="$2" entry_hash entry_name
  while IFS= read -r entry_hash || [ -n "$entry_hash" ]; do
    case "$entry_hash" in ''|'#'*) continue ;; esac
    entry_name=${entry_hash#*  }
    [ "$entry_name" = "$entry_hash" ] && continue
    [ "$entry_name" = "$wanted" ] && return 0
  done < "$expected_file"
  return 1
}

verify_tree() {
  local dir="$1" expected="$2" owner_file="$3" expected_owner=0:0
  local count=0 files=0 failures=0 line hash name path actual_hash details dir_details dir_owner
  if [ ! -f "$expected" ] || [ ! -r "$expected" ]; then
    echo "FAIL: expected-hashes file missing or unreadable: $expected"; return 1
  fi
  if [ ! -d "$dir" ] || [ -L "$dir" ]; then echo "FAIL: manifest directory missing or symlink: $dir"; return 1; fi
  if [ -n "$owner_file" ]; then
    if [ ! -r "$owner_file" ]; then echo "FAIL: owner expectation missing: $owner_file"; return 1; fi
    expected_owner=$(cat "$owner_file")
    case "$expected_owner" in *:*) ;; *) echo "FAIL: malformed owner expectation"; return 1 ;; esac
    echo "INFO: non-root fixture owner expectation from stat: $expected_owner"
  fi
  dir_details=$(file_details "$dir")
  dir_owner=${dir_details#* }
  if [ "${dir_details%% *}" != 700 ] || [ "$dir_owner" != "$expected_owner" ]; then
    echo "FAIL: manifest directory attributes=$dir_details expected=700 $expected_owner"; failures=$((failures + 1))
  else
    echo "PASS: manifest directory owner/mode ($dir_details)"
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    hash=${line%% *}; name=${line#*  }
    if [ "$hash" = "$line" ] || [ "$name" = "$line" ] || ! [[ "$hash" =~ ^[0-9a-f]{64}$ ]] ||
       [ -z "$name" ] || [ "$name" = "$line" ] || [[ "$name" == */* ]]; then
      echo "FAIL: malformed expected-hashes line: $line"; failures=$((failures + 1)); continue
    fi
    count=$((count + 1)); path="$dir/$name"
    if [ ! -f "$path" ] || [ -L "$path" ]; then echo "FAIL: missing or non-regular manifest: $name"; failures=$((failures + 1)); continue; fi
    actual_hash=$(file_hash "$path")
    if [ "$actual_hash" != "$hash" ]; then echo "FAIL: SHA-256 mismatch: $name"; failures=$((failures + 1)); else echo "PASS: SHA-256 $name"; fi
    details=$(file_details "$path")
    if [ "$details" != "600 $expected_owner" ]; then echo "FAIL: $name attributes=$details expected=600 $expected_owner"; failures=$((failures + 1)); else echo "PASS: owner/mode $name ($details)"; fi
  done < "$expected"
  if [ "$count" -eq 0 ]; then echo "FAIL: expected-hashes file contains no manifest entries"; failures=$((failures + 1)); fi
  for path in "$dir"/* "$dir"/.[!.]* "$dir"/..?*; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    files=$((files + 1)); name=${path##*/}
    if ! expected_contains_name "$name" "$expected"; then
      echo "FAIL: unexpected manifest directory entry: $name"; failures=$((failures + 1))
    fi
  done
  echo "Evaluated: expected manifests=$count, directory entries=$files, failures=$failures"
  [ "$files" -gt 0 ] || { echo "FAIL: manifest directory is empty"; failures=$((failures + 1)); }
  [ "$failures" -eq 0 ]
}

if [ "$SELF_TEST" -eq 1 ]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  make_fixture() {
    local tag="$1" content="$2" dir
    dir="$tmp/$tag/etc/kubernetes/manifests"
    mkdir -p "$dir"; chmod 700 "$dir"; printf '%s\n' "$content" > "$dir/kube-apiserver.yaml"; chmod 600 "$dir/kube-apiserver.yaml"
    file_hash "$dir/kube-apiserver.yaml" | awk -v n=kube-apiserver.yaml '{print $1 "  " n}' > "$tmp/$tag.expected"
    file_owner "$dir/kube-apiserver.yaml" > "$tmp/$tag.owner"
  }
  make_fixture good accepted
  verify_tree "$tmp/good/etc/kubernetes/manifests" "$tmp/good.expected" "$tmp/good.owner"
  echo 'PASS: good fixture accepted'
  if "$0" --root "$tmp/good" --expected "$tmp/good.expected" --owner-expectation "$tmp/good.owner"; then echo 'PASS: --root CLI path accepted'; else echo 'FAIL: --root CLI path rejected'; exit 1; fi
  make_fixture wrong-mode accepted; chmod 644 "$tmp/wrong-mode/etc/kubernetes/manifests/kube-apiserver.yaml"
  if verify_tree "$tmp/wrong-mode/etc/kubernetes/manifests" "$tmp/wrong-mode.expected" "$tmp/wrong-mode.owner"; then echo 'FAIL: wrong mode accepted'; exit 1; else echo 'PASS: wrong mode detected'; fi
  make_fixture extra accepted; printf 'extra\n' > "$tmp/extra/etc/kubernetes/manifests/backup"
  if verify_tree "$tmp/extra/etc/kubernetes/manifests" "$tmp/extra.expected" "$tmp/extra.owner"; then echo 'FAIL: extra file accepted'; exit 1; else echo 'PASS: extra file detected'; fi
  make_fixture changed accepted; printf 'changed\n' > "$tmp/changed/etc/kubernetes/manifests/kube-apiserver.yaml"
  if verify_tree "$tmp/changed/etc/kubernetes/manifests" "$tmp/changed.expected" "$tmp/changed.owner"; then echo 'FAIL: changed content accepted'; exit 1; else echo 'PASS: changed content detected'; fi
  if verify_tree "$tmp/missing/etc/kubernetes/manifests" "$tmp/good.expected" "$tmp/good.owner"; then echo 'FAIL: missing dir accepted'; exit 1; else echo 'PASS: missing dir detected'; fi
  make_fixture readable-dir accepted; chmod 755 "$tmp/readable-dir/etc/kubernetes/manifests"
  if verify_tree "$tmp/readable-dir/etc/kubernetes/manifests" "$tmp/readable-dir.expected" "$tmp/readable-dir.owner"; then echo 'FAIL: world-readable dir accepted'; exit 1; else echo 'PASS: world-readable dir detected'; fi
  make_fixture symlink accepted; rm "$tmp/symlink/etc/kubernetes/manifests/kube-apiserver.yaml"; ln -s /dev/null "$tmp/symlink/etc/kubernetes/manifests/kube-apiserver.yaml"
  if verify_tree "$tmp/symlink/etc/kubernetes/manifests" "$tmp/symlink.expected" "$tmp/symlink.owner"; then echo 'FAIL: symlink accepted'; exit 1; else echo 'PASS: symlink detected'; fi
  make_fixture malformed accepted; printf 'not-a-hash  kube-apiserver.yaml\n' > "$tmp/malformed.expected"
  if verify_tree "$tmp/malformed/etc/kubernetes/manifests" "$tmp/malformed.expected" "$tmp/malformed.owner"; then echo 'FAIL: malformed hash line accepted'; exit 1; else echo 'PASS: malformed hash line detected'; fi
  make_fixture comments accepted; printf '# comment only\n\n' > "$tmp/comments.expected"
  if verify_tree "$tmp/comments/etc/kubernetes/manifests" "$tmp/comments.expected" "$tmp/comments.owner"; then echo 'FAIL: comments-only expected file accepted'; exit 1; else echo 'PASS: comments-only expected file detected'; fi
  make_fixture backslash accepted
  mv "$tmp/backslash/etc/kubernetes/manifests/kube-apiserver.yaml" "$tmp/backslash/etc/kubernetes/manifests/odd\\name"
  chmod 600 "$tmp/backslash/etc/kubernetes/manifests/odd\\name"
  file_hash "$tmp/backslash/etc/kubernetes/manifests/odd\\name" | awk '{print $1 "  odd\\name"}' > "$tmp/backslash.expected"
  if verify_tree "$tmp/backslash/etc/kubernetes/manifests" "$tmp/backslash.expected" "$tmp/backslash.owner"; then echo 'PASS: backslash filename matched exactly'; else echo 'FAIL: backslash filename rejected'; exit 1; fi
  mkdir -p "$tmp/empty/etc/kubernetes/manifests"
  if verify_tree "$tmp/empty/etc/kubernetes/manifests" "$tmp/good.expected" "$tmp/good.owner"; then echo 'FAIL: empty dir accepted'; exit 1; else echo 'PASS: empty dir detected'; fi
  echo 'PASS: all static-pod manifest self-tests'
  exit 0
fi

[ -n "$EXPECTED" ] || { echo "FAIL: --expected FILE is required"; exit 2; }
verify_tree "$MANIFEST_DIR" "$EXPECTED" "$OWNER_EXPECTATION"
