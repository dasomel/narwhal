#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 dasomel
#=========================================================================
# refresh-component-licenses.sh
# Re-resolve component-licenses.tsv from each upstream repository.
#
# WHY: the map is what makes the bundle SBOM's licenses[] trustworthy, and a
# hand-maintained license table rots — projects relicense (Grafana went AGPL,
# Redis went tri-license) and nothing in the build notices. This re-asks the
# source of truth so a relicensing shows up as a diff instead of as a wrong SPDX
# id repeated in every bundle.
#
# Rows carrying a note in column 4 are NOT re-resolved. Those are the ones GitHub
# cannot classify — a per-file SPDX project, a tri-license, an upstream that is
# not on GitHub — and were read out of the upstream LICENSE by hand. Letting the
# API overwrite them with NOASSERTION would lose the only researched rows.
#
# Requires: gh (authenticated). Needs network — this is a maintenance tool, not
# part of the air-gapped build path.
#
# USAGE: scripts/airgap/lib/refresh-component-licenses.sh [--check]
#   --check   exit 1 if anything changed, printing the diff (for CI)
#=========================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAP="${HERE}/component-licenses.tsv"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

command -v gh >/dev/null || { echo "gh is required" >&2; exit 2; }
gh auth status >/dev/null 2>&1 || { echo "gh is not authenticated" >&2; exit 2; }

TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT

changed=0
while IFS= read -r line; do
  # Comments, the column header and blank lines pass through untouched.
  case "${line}" in ''|'#'*) printf '%s\n' "${line}" >> "${TMP}"; continue ;; esac

  img="$(printf '%s' "${line}" | cut -f1)"
  upstream="$(printf '%s' "${line}" | cut -f2)"
  spdx="$(printf '%s' "${line}" | cut -f3)"
  note="$(printf '%s' "${line}" | cut -f4)"

  if [ -n "${note}" ]; then
    printf '%s\n' "${line}" >> "${TMP}"
    continue
  fi

  fresh="$(gh api "repos/${upstream}/license" --jq '.license.spdx_id' 2>/dev/null || true)"
  if [ -z "${fresh}" ] || [ "${fresh}" = "NOASSERTION" ]; then
    # Keep the recorded value and say so; an unclassifiable repo is a research
    # task, not a reason to blank a row that was already correct.
    echo "WARN: ${upstream} is unclassifiable upstream; keeping ${spdx}" >&2
    printf '%s\n' "${line}" >> "${TMP}"
    continue
  fi

  if [ "${fresh}" != "${spdx}" ]; then
    echo "CHANGED: ${img}  ${spdx} -> ${fresh}" >&2
    changed=1
  fi
  printf '%s\t%s\t%s\t\n' "${img}" "${upstream}" "${fresh}" >> "${TMP}"
done < "${MAP}"

if [ "${CHECK}" -eq 1 ]; then
  if diff -u "${MAP}" "${TMP}"; then
    echo "component-licenses.tsv is current"
    exit 0
  fi
  echo "component-licenses.tsv is stale — run without --check and review the diff" >&2
  exit 1
fi

cp "${TMP}" "${MAP}"
[ "${changed}" -eq 1 ] && echo "map updated — review the diff before committing" || echo "no license changes upstream"
