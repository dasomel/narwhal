#!/usr/bin/env bash
# R308 (D-GITOPS-PUSH-DIRS, D-MIRROR-ALL-NONDESTRUCTIVE, D-EMPTY-ARRAY-GUARD):
# push-to-gitea.sh directory mirroring, deletion propagation, single-file handling,
# '..' / Gitea-only top-level directory rejection, and the no-path modes (mirror-all,
# --tag, --rollback) under /bin/bash. Exercises GITEA_REMOTE_URL against a local bare repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${ROOT}"
SCRIPT="${ROOT}/scripts/gitops/push-to-gitea.sh"
# D-R308-BASH32: run the script under /bin/bash explicitly (bash 3.2 on macOS) so an
# empty-array/`set -u` regression is caught even when the shell running this test is bash 5.
# Falls back to PATH bash where /bin/bash does not exist.
BASH_BIN="/bin/bash"; [[ -x "${BASH_BIN}" ]] || BASH_BIN="bash"

fail() { echo "FAIL: $*" >&2; exit 1; }

run_suite() {
  local target_script="$1"
  local tmp
  tmp="$(mktemp -d)"
  # Clean up temp dir on exit of run_suite
  trap 'rm -rf "${tmp}"' EXIT

  # The publisher refreshes ArgoCD after a local push: never use the host kubeconfig.
  mkdir -p "${tmp}/bin"
  printf '#!/bin/sh\nexit 0\n' > "${tmp}/bin/kubectl"
  chmod +x "${tmp}/bin/kubectl"
  local PATH="${tmp}/bin:${PATH}"

  local bare="${tmp}/remote.git"
  git init --bare -q --initial-branch=main "${bare}"

  local gitops="${tmp}/gitops"
  mkdir -p "${gitops}/resources/mytheme" "${gitops}/apps"
  echo "body { color: blue; }" > "${gitops}/resources/mytheme/style.css"
  echo "temp file" > "${gitops}/resources/mytheme/victim.txt"
  echo "kind: Application" > "${gitops}/apps/single.yaml"

  local env_common=(
    "GITEA_REMOTE_URL=${bare}"
    "GITOPS_DIR=${gitops}"
    "HOME=${tmp}"   # keep --tag/--rollback evidence out of the real ~/.narwhal
  )

  # 1. Initial push of directory
  if ! env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "init theme" "resources/mytheme" >"${tmp}/out" 2>&1; then
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

  if ! env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "update theme" "resources/mytheme" >"${tmp}/out" 2>&1; then
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
  if ! env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "push single file" "apps/single.yaml" >"${tmp}/out" 2>&1; then
    cat "${tmp}/out" >&2
    return 1
  fi
  local files_v3
  files_v3="$(git --git-dir="${bare}" ls-tree -r --name-only main)"
  echo "${files_v3}" | grep -qx "apps/single.yaml" || return 1

  # 4. Refuse '..' and absolute paths
  local out_err
  if env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "refuse dotdot" ".." >"${tmp}/out" 2>&1; then
    echo "push-to-gitea.sh accepted '..' path" >&2
    return 1
  fi
  out_err="$(cat "${tmp}/out")"
  echo "${out_err}" | grep -qE "path(s)? must be relative to gitops/ and cannot contain '\.\.'" || return 1

  if env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "refuse absolute" "/etc/hosts" >"${tmp}/out" 2>&1; then
    echo "push-to-gitea.sh accepted absolute path" >&2
    return 1
  fi

  # 5. Refuse top-level directories that hold Gitea-only content (D-REFUSE-GITEA-ONLY-DIRS)
  local top
  for top in "resources" "resources/" "resources//" "./resources" "resources/tenants" "resources//tenants/"; do
    if env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "refuse top" "${top}" >"${tmp}/out" 2>&1; then
      echo "push-to-gitea.sh accepted Gitea-only directory '${top}'" >&2
      return 1
    fi
    grep -q "Gitea-only content" "${tmp}/out" || return 1
  done

  # 6. No-path modes. On bash 3.2 an empty PATHS under `set -u` aborted all of these.
  echo "mirror-all" > "${gitops}/apps/all.yaml"
  if ! env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "mirror all" >"${tmp}/out" 2>&1; then
    cat "${tmp}/out" >&2
    echo "mirror-all failed" >&2
    return 1
  fi
  git --git-dir="${bare}" ls-tree -r --name-only main | grep -qx "apps/all.yaml" || return 1

  if ! env "${env_common[@]}" "${BASH_BIN}" "${target_script}" --tag v0.0.0-test >"${tmp}/out" 2>&1; then
    cat "${tmp}/out" >&2
    echo "--tag failed" >&2
    return 1
  fi
  git --git-dir="${bare}" rev-parse -q --verify "refs/tags/v0.0.0-test^{commit}" >/dev/null || return 1

  # --rollback: move main past the tag, then roll back; tree must equal the tag's tree.
  echo "after tag" > "${gitops}/apps/later.yaml"
  env "${env_common[@]}" "${BASH_BIN}" "${target_script}" "later" "apps/later.yaml" >"${tmp}/out" 2>&1 || return 1
  if ! env "${env_common[@]}" "${BASH_BIN}" "${target_script}" --rollback v0.0.0-test >"${tmp}/out" 2>&1; then
    cat "${tmp}/out" >&2
    echo "--rollback failed" >&2
    return 1
  fi
  [[ "$(git --git-dir="${bare}" rev-parse 'main^{tree}')" == "$(git --git-dir="${bare}" rev-parse 'v0.0.0-test^{tree}')" ]] || return 1

  rm -rf "${tmp}"
  trap - EXIT
  return 0
}

# Run clean verification against actual script
run_suite "${SCRIPT}" || fail "suite failed on clean push-to-gitea.sh"

# Mutation copies keep the real dependency paths; a missing helper is not a caught mutation.
copy_mutant() {
  cp "${SCRIPT}" "$1"
  python3 - "$1" "${ROOT}" <<'PYCODE'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
start = text.index('REPO_ROOT=')
end = text.index('\n', start)
path.write_text(text[:start] + 'REPO_ROOT="' + sys.argv[2] + '"' + text[end:])
PYCODE
}

# Mutation 1: Bug 1 & 2 regression (reintroduce cp -r without rm -rf)
mut1="$(mktemp -d)"
trap 'rm -rf "${mut1}"' EXIT
copy_mutant "${mut1}/push-to-gitea.sh"
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
copy_mutant "${mut2}/push-to-gitea.sh"
python3 -c "
with open('${mut2}/push-to-gitea.sh') as f:
    s = f.read()
start = s.index('if [[ \${#PATHS[@]} -gt 0 ]]; then\n  for p in')
end = s.index('  done\nfi\n', start) + len('  done\nfi\n')
target = s[start:end]
assert target in s, 'validation block not found'
s = s.replace(target, '# validation removed', 1)
with open('${mut2}/push-to-gitea.sh', 'w') as f:
    f.write(s)
"
if run_suite "${mut2}/push-to-gitea.sh" >/dev/null 2>&1; then
  fail "mutation 2 (removed path validation) was not caught"
fi

# Mutation 3: remove the empty-array guard (bash 3.2 `set -u` regression). Only observable
# where /bin/bash is 3.x; on bash >= 4 the unguarded loop is valid, so it is skipped there.
mut3_note="3 mutations caught"
if [[ "$("${BASH_BIN}" -c 'echo "${BASH_VERSINFO[0]}"')" -lt 4 ]]; then
  mut3="$(mktemp -d)"
  trap 'rm -rf "${mut1}" "${mut2}" "${mut3}"' EXIT
  copy_mutant "${mut3}/push-to-gitea.sh"
  python3 -c "
with open('${mut3}/push-to-gitea.sh') as f:
    s = f.read()
target = 'if [[ \${#PATHS[@]} -gt 0 ]]; then\n  for p in'
assert target in s, 'guard not found'
s = s.replace(target, 'if true; then\n  for p in', 1)
with open('${mut3}/push-to-gitea.sh', 'w') as f:
    f.write(s)
"
  if run_suite "${mut3}/push-to-gitea.sh" >/dev/null 2>&1; then
    fail "mutation 3 (removed empty-array guard) was not caught"
  fi
  rm -rf "${mut3}"
else
  mut3_note="2 mutations caught, guard mutation skipped: ${BASH_BIN} is bash >= 4"
fi

rm -rf "${mut1}" "${mut2}"
trap - EXIT

echo "R308 ok: push-to-gitea directory mirroring, deletions, single-file, path validation, no-path modes (${mut3_note})"
