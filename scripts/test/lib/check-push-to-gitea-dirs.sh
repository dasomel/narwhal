#!/usr/bin/env bash
# R308 (D-GITOPS-PUSH-DIRS, D-MIRROR-ALL-NONDESTRUCTIVE):
# push-to-gitea.sh directory mirroring, deletion propagation, single-file handling,
# and '..' path rejection. Exercises GITEA_REMOTE_URL against a local bare repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${ROOT}"
SCRIPT="${ROOT}/scripts/gitops/push-to-gitea.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

run_suite() {
  local target_script="$1"
  local tmp
  tmp="$(mktemp -d)"
  # Clean up temp dir on exit of run_suite
  trap 'rm -rf "${tmp}"' EXIT

  local bare="${tmp}/remote.git"
  git init --bare -q "${bare}"

  local gitops="${tmp}/gitops"
  mkdir -p "${gitops}/resources/mytheme" "${gitops}/apps"
  echo "body { color: blue; }" > "${gitops}/resources/mytheme/style.css"
  echo "temp file" > "${gitops}/resources/mytheme/victim.txt"
  echo "kind: Application" > "${gitops}/apps/single.yaml"

  local env_common=(
    "GITEA_REMOTE_URL=${bare}"
    "GITOPS_DIR=${gitops}"
  )

  # 1. Initial push of directory
  if ! env "${env_common[@]}" bash "${target_script}" "init theme" "resources/mytheme" >"${tmp}/out" 2>&1; then
    cat "${tmp}/out" >&2
    return 1
  fi

  local files_v1
  files_v1="$(git --git-dir="${bare}" ls-tree -r --name-only main)"
  echo "${files_v1}" | grep -qx "resources/mytheme/style.css" || return 1
  echo "${files_v1}" | grep -qx "resources/mytheme/victim.txt" || return 1

  # 2. Update directory: modify style.css, delete victim.txt, add new.txt
  echo "body { color: red; }" > "${gitops}/resources/mytheme/style.css"
  rm -f "${gitops}/resources/mytheme/victim.txt"
  echo "new file" > "${gitops}/resources/mytheme/new.txt"

  if ! env "${env_common[@]}" bash "${target_script}" "update theme" "resources/mytheme" >"${tmp}/out" 2>&1; then
    cat "${tmp}/out" >&2
    return 1
  fi

  local files_v2
  files_v2="$(git --git-dir="${bare}" ls-tree -r --name-only main)"
  # Check: no nesting (Bug 1)
  if echo "${files_v2}" | grep -q "resources/mytheme/mytheme"; then
    echo "directory was nested (Bug 1): found resources/mytheme/mytheme" >&2
    return 1
  fi
  # Check: deletion propagated (Bug 2)
  if echo "${files_v2}" | grep -qx "resources/mytheme/victim.txt"; then
    echo "deleted file was not removed from Gitea (Bug 2)" >&2
    return 1
  fi
  # Check: updated & added files present
  echo "${files_v2}" | grep -qx "resources/mytheme/style.css" || return 1
  echo "${files_v2}" | grep -qx "resources/mytheme/new.txt" || return 1
  local content
  content="$(git --git-dir="${bare}" show main:resources/mytheme/style.css)"
  [[ "${content}" == "body { color: red; }" ]] || return 1

  # 3. Single-file path still works
  if ! env "${env_common[@]}" bash "${target_script}" "push single file" "apps/single.yaml" >"${tmp}/out" 2>&1; then
    cat "${tmp}/out" >&2
    return 1
  fi
  local files_v3
  files_v3="$(git --git-dir="${bare}" ls-tree -r --name-only main)"
  echo "${files_v3}" | grep -qx "apps/single.yaml" || return 1

  # 4. Refuse '..' and absolute paths
  local out_err
  if env "${env_common[@]}" bash "${target_script}" "refuse dotdot" ".." >"${tmp}/out" 2>&1; then
    echo "push-to-gitea.sh accepted '..' path" >&2
    return 1
  fi
  out_err="$(cat "${tmp}/out")"
  echo "${out_err}" | grep -qE "path(s)? must be relative to gitops/ and cannot contain '\.\.'" || return 1

  if env "${env_common[@]}" bash "${target_script}" "refuse absolute" "/etc/hosts" >"${tmp}/out" 2>&1; then
    echo "push-to-gitea.sh accepted absolute path" >&2
    return 1
  fi

  rm -rf "${tmp}"
  trap - EXIT
  return 0
}

# Run clean verification against actual script
run_suite "${SCRIPT}" || fail "suite failed on clean push-to-gitea.sh"

# Mutation 1: Bug 1 & 2 regression (reintroduce cp -r without rm -rf)
mut1="$(mktemp -d)"
trap 'rm -rf "${mut1}"' EXIT
cp "${SCRIPT}" "${mut1}/push-to-gitea.sh"
# Replace directory mirror logic with old buggy cp -r
python3 -c "
with open('${mut1}/push-to-gitea.sh') as f:
    s = f.read()
old_buggy = '''      if [[ -d \"\${src}\" ]]; then
        mkdir -p \"\$(dirname \"\${rel}\")\"
        cp -r \"\${src}\" \"\${rel}\"
        log \"copied gitops/\${rel} -> \${rel}\"'''
target = '''      if [[ -d \"\${src}\" ]]; then
        rm -rf \"\${rel}\"
        mkdir -p \"\$(dirname \"\${rel}\")\"
        cp -R \"\${src}\" \"\${rel}\"
        log \"mirrored directory gitops/\${rel} -> \${rel}\"'''
assert target in s, 'target block not found'
s = s.replace(target, old_buggy, 1)
with open('${mut1}/push-to-gitea.sh', 'w') as f:
    f.write(s)
"
if run_suite "${mut1}/push-to-gitea.sh" >/dev/null 2>&1; then
  fail "mutation 1 (reverted directory mirroring) was not caught"
fi

# Mutation 2: Remove '..' and absolute path refusal check
mut2="$(mktemp -d)"
trap 'rm -rf "${mut1}" "${mut2}"' EXIT
cp "${SCRIPT}" "${mut2}/push-to-gitea.sh"
python3 -c "
with open('${mut2}/push-to-gitea.sh') as f:
    s = f.read()
target = '''# Refuse paths containing '..' or absolute paths
for p in \"\${PATHS[@]}\"; do
  if [[ -z \"\${p}\" || \"\${p}\" = /* || \"\${p}\" == *\"..\"* || \"\${p}\" == \".\" ]]; then
    die \"path must be relative to gitops/ and cannot contain '..': \${p}\"
  fi
done'''
assert target in s, 'validation block not found'
s = s.replace(target, '# validation removed', 1)
with open('${mut2}/push-to-gitea.sh', 'w') as f:
    f.write(s)
"
if run_suite "${mut2}/push-to-gitea.sh" >/dev/null 2>&1; then
  fail "mutation 2 (removed path validation) was not caught"
fi

rm -rf "${mut1}" "${mut2}"
trap - EXIT

echo "R308 ok: push-to-gitea directory mirroring, deletions, single-file, path validation (2 mutations caught)"
