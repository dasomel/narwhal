#!/usr/bin/env bash
# SessionStart Hook: Load project context + set environment variables
set -euo pipefail

# Inject environment variables via CLAUDE_ENV_FILE (available in Bash commands)
if [[ -n "${CLAUDE_ENV_FILE:-}" ]]; then
  cat >> "${CLAUDE_ENV_FILE}" <<ENVEOF
export NARWHAL_PROJECT=true
export NARWHAL_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
ENVEOF
fi

# Pass project context to Claude
jq -n '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: "Narwhal IDP project. K8s 1.35, 2-phase provisioning, HTTPS OIDC required. Master 4GB (control-plane only), Worker 6GB (platform apps). Use vagrant ssh master-1."
  }
}'
