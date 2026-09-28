#!/usr/bin/env bash
# Convert tag@digest refs to the Docker reference form accepted by skopeo.
skopeo_source_ref() {
  local ref="$1" tagged digest repository
  if [[ "${ref}" == *@sha256:* ]]; then
    tagged="${ref%@sha256:*}"
    digest="${ref##*@}"
    repository="${tagged%:*}"
    printf 'docker://%s@%s\n' "${repository}" "${digest}"
  else
    printf 'docker://%s\n' "${ref}"
  fi
}
