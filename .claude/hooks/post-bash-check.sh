#!/usr/bin/env bash
# PostToolUse Hook: Post-bash command validation
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "${INPUT}" | jq -r '.tool_name // empty')
COMMAND=$(echo "${INPUT}" | jq -r '.tool_input.command // empty')
EXIT_CODE=$(echo "${INPUT}" | jq -r '.tool_response.exit_code? // "0"')

# Ignore successful commands
if [[ "${EXIT_CODE}" == "0" ]]; then
  exit 0
fi

CONTEXT=""

# vagrant ssh failure
if [[ "${COMMAND}" == *"vagrant ssh"* ]]; then
  CONTEXT="[Hint] vagrant ssh failed. Check if VM is running: vagrant status. Use vagrant ssh for access (not direct SSH)."
fi

# kubectl failure
if [[ "${COMMAND}" == *"kubectl"* ]]; then
  CONTEXT="[Hint] kubectl failed. Check KUBECONFIG settings. Verify if command should run inside VM. Use: vagrant ssh master-1 -c \"kubectl ...\""
fi

# helm failure
if [[ "${COMMAND}" == *"helm"* ]]; then
  CONTEXT="[Hint] helm failed. Check ARM64 image compatibility. Consider removing --wait flag. Verify chart version matches VERSIONS.md."
fi

if [[ -n "${CONTEXT}" ]]; then
  jq -n --arg ctx "${CONTEXT}" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $ctx
    }
  }'
fi

exit 0
