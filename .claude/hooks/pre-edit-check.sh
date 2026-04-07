#!/usr/bin/env bash
# PreToolUse Hook: Pre-edit validation
# Uses JSON output with hookSpecificOutput.permissionDecision (official protocol)
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // empty')

if [[ -z "${FILE_PATH}" ]]; then
  exit 0
fi

# Prevent .vagrant folder modification
if [[ "${FILE_PATH}" == *".vagrant/"* ]]; then
  jq -n '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: "Cannot directly modify .vagrant/ folder."
    }
  }'
  exit 0
fi

# Warn on sensitive files (ask for user confirmation)
case "${FILE_PATH}" in
  *kubeconfig*|*.pem|*.key|*.env)
    jq -n '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "ask",
        permissionDecisionReason: "Sensitive file detected. No hardcoded passwords/tokens!"
      }
    }'
    exit 0
    ;;
esac

exit 0
