#!/usr/bin/env bash
# PostToolUse Hook: Notify about related files when a file is modified
# Passes context to Claude via additionalContext (plain echo only visible in verbose)
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // empty')

if [[ -z "${FILE_PATH}" ]]; then
  exit 0
fi

CONTEXT=""

case "${FILE_PATH}" in
  */scripts/cluster/*.sh)
    CONTEXT="[Reminder] Check version sync with VERSIONS.md. Verify Vagrantfile environment variables."
    ;;
  */scripts/common/*.sh)
    CONTEXT="[Reminder] This is a common script applied to all nodes."
    ;;
  */gitops/apps/*.yaml)
    CONTEXT="[Reminder] Check sync with gitops/values/ or gitops/resources/. ArgoCD auto-syncs."
    ;;
  */gitops/resources/*.yaml)
    CONTEXT="[Reminder] Check related app YAML."
    ;;
  */Vagrantfile)
    CONTEXT="[Reminder] Check sync with environment variables referenced in scripts and VERSIONS.md."
    ;;
  */VERSIONS.md)
    CONTEXT="[Reminder] Update related script versions and gitops/apps/*.yaml chart versions."
    ;;
  *)
    exit 0
    ;;
esac

if [[ -n "${CONTEXT}" ]]; then
  jq -n --arg ctx "${CONTEXT}" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $ctx
    }
  }'
fi
