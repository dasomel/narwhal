#!/bin/bash
set -euo pipefail
# image-classes.sh — shared image-classification regexes for the airgap scripts
# (narwhal#52 review follow-up)
#
# WHY: INCLUSTER_BUILT_RE used to be hand-copied into three scripts
# (01-generate-image-list.sh, 09-verify-bundle-completeness.sh,
# refresh-image-digests.sh) with a comment on each asking the next editor to keep
# them in sync BY HAND. A single source of truth removes that failure mode outright
# — R138/R138b in regression-check-kakao.sh assert no script defines its own copy.
#
# Usage: source "${SCRIPT_DIR}/lib/image-classes.sh" (from scripts/airgap/*.sh) or
#        source "${HERE}/image-classes.sh" (from scripts/airgap/lib/*.sh)

# In-cluster-built image: produced by Kaniko from source pushed to Gitea, NOT
# pulled from any upstream registry — must never be in a pull-based mirror list,
# and is the only image allowed to carry an UNRESOLVED digest row instead of a
# real sha256 in image-digests.tsv.
# shellcheck disable=SC2034  # consumed by the three scripts that source this file, not here
INCLUSTER_BUILT_RE='harbor\.local\.narwhal\.internal/library/narwhal-portal'
