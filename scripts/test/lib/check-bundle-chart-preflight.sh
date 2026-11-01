#!/usr/bin/env bash
# R300 (D-BUNDLE-PIN-VERSION): scripts/up.sh runs scripts/airgap/check-bundle.sh, which must
# reject a bundle whose charts/ lacks any GitOps chart pin at the pinned version (stale bundle
# after a pin bump: 2026-11-02 falco 9.2.1 / headlamp 0.46.0). Runs the real check-bundle.sh
# against temp bundles; no network, no cluster.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${ROOT}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

# up.sh must call the bundle check, and not as a comment.
calls_check() { grep -Eq '^[[:space:]]*"\$\{PWD\}/scripts/airgap/check-bundle\.sh"' "$1"; }
calls_check scripts/up.sh || fail "scripts/up.sh does not call scripts/airgap/check-bundle.sh"

# Build a complete bundle: every name-level requirement plus every GitOps pin.
bundle="${tmp}/bundle"
mkdir -p "${bundle}/charts" "${bundle}/bin" "${bundle}/manifests"
while IFS=$'\t' read -r kind name; do
  case "${kind}" in
    charts)    : > "${bundle}/charts/${name}-0.0.0.tgz" ;;
    manifests) : > "${bundle}/manifests/${name}" ;;
    bin)       : > "${bundle}/bin/${name}"; chmod +x "${bundle}/bin/${name}" ;;
  esac
done < <(python3 scripts/airgap/lib/scan-bundle-requirements.py "${ROOT}" 2>/dev/null)
pins="${tmp}/pins"
python3 scripts/airgap/lib/discover-charts.py "${ROOT}" scripts/airgap/lib/chart-upstream-sources.tsv --gitops-only > "${pins}"
[ -s "${pins}" ] || fail "discover-charts.py --gitops-only returned no pins"
while IFS='|' read -r _ _ chart version _; do : > "${bundle}/charts/${chart}-${version}.tgz"; done < "${pins}"

check() { AIRGAP_BUNDLE_DIR="${bundle}" bash "scripts/airgap/check-bundle.sh" >"${tmp}/out" 2>&1; }

# 1. complete bundle passes
check || { cat "${tmp}/out" >&2; fail "complete bundle rejected"; }

victim="$(awk -F'|' '$3=="falco"{print $3 "-" $4 ".tgz"; exit}' "${pins}")"
[ -n "${victim}" ] || fail "falco is no longer a GitOps pin; pick another chart for this case"

# 2. one pin missing -> fails and names it
mv "${bundle}/charts/${victim}" "${tmp}/${victim}"
if check; then fail "bundle missing ${victim} accepted"; fi
grep -Fq "charts/${victim}" "${tmp}/out" || { cat "${tmp}/out" >&2; fail "output does not name ${victim}"; }
grep -Fq "03-save-helm-charts.sh" "${tmp}/out" || fail "output does not name the fix"

# 3. stale version (older file for the same chart) -> fails and names the pinned one
: > "${bundle}/charts/falco-0.0.1.tgz"
if check; then fail "stale falco bundle accepted"; fi
grep -Fq "charts/${victim}" "${tmp}/out" || fail "stale case does not name ${victim}"

# 4. escape hatch skips only the version check
NARWHAL_SKIP_BUNDLE_PREFLIGHT=1 check || fail "NARWHAL_SKIP_BUNDLE_PREFLIGHT=1 did not skip"

# 5. mutations: each must be caught
# (a) version block removed: the mutant must accept the stale bundle the real script rejects
# shellcheck disable=SC2016 # the sed pattern is literal
sed -e '/^if \[ "\${NARWHAL_SKIP_BUNDLE_PREFLIGHT/,/^fi$/d' \
    -e "s|^SCRIPT_DIR=.*|SCRIPT_DIR=\"${ROOT}/scripts/airgap\"|" scripts/airgap/check-bundle.sh > "${tmp}/mut-check.sh"
AIRGAP_BUNDLE_DIR="${bundle}" bash "${tmp}/mut-check.sh" >/dev/null 2>&1 \
  || fail "mutation (version block removed) not caught: mutant should accept the stale bundle"
# (b) up.sh call removed
sed -E '/scripts\/airgap\/check-bundle\.sh/d' scripts/up.sh > "${tmp}/mut-up.sh"
if calls_check "${tmp}/mut-up.sh"; then fail "mutation (up.sh call removed) not caught"; fi

echo "R300 ok: bundle preflight (pass, missing, stale, escape hatch, 2 mutations)"
