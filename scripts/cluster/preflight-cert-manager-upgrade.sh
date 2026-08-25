#!/usr/bin/env bash
set -euo pipefail

# Validate the GitOps declarations needed before proposing a cert-manager upgrade.
# This intentionally examines only desired state. A live gate must separately confirm
# Ready replicas, PDB allowed disruptions, webhook reachability, and Certificate health.

MANIFEST="gitops/charts/narwhal-apps/templates/cert-manager.yaml"

if [ "$#" -gt 1 ]; then
  echo "usage: $0 [cert-manager-application.yaml]" >&2
  exit 2
fi
if [ "$#" -eq 1 ]; then
  MANIFEST="$1"
fi

if [ ! -f "${MANIFEST}" ]; then
  echo "ERROR: cert-manager Application manifest not found: ${MANIFEST}" >&2
  exit 2
fi
if ! command -v yq >/dev/null 2>&1; then
  echo "ERROR: yq is required to inspect ${MANIFEST}" >&2
  exit 2
fi

FAILURES=0

require() {
  local description="$1"
  local expression="$2"

  if yq -e "${expression}" "${MANIFEST}" >/dev/null; then
    echo "PASS: ${description}"
  else
    echo "FAIL: ${description}" >&2
    FAILURES=$((FAILURES + 1))
  fi
}

require "manifest is the cert-manager Argo CD Application" \
  '.kind == "Application" and .metadata.name == "cert-manager"'
require "chart targetRevision is an immutable-looking version pin" \
  '.spec.source.chart == "cert-manager" and (.spec.source.targetRevision | test("^v[0-9]+\\.[0-9]+\\.[0-9]+$"))'

component_preflight() {
  local component="$1"
  local values_path="$2"

  require "${component} declares at least two replicas" \
    "${values_path}.replicaCount >= 2"
  require "${component} uses a one-at-a-time RollingUpdate" \
    "${values_path}.strategy.type == \"RollingUpdate\" and ${values_path}.strategy.rollingUpdate.maxSurge == 1 and ${values_path}.strategy.rollingUpdate.maxUnavailable == 1"
  require "${component} declares a PDB with minAvailable=1" \
    "${values_path}.podDisruptionBudget.enabled == true and ${values_path}.podDisruptionBudget.minAvailable == 1"
  require "${component} declares hostname DoNotSchedule topology spread" \
    "${values_path}.topologySpreadConstraints[] | select(.maxSkew == 1 and .topologyKey == \"kubernetes.io/hostname\" and .whenUnsatisfiable == \"DoNotSchedule\" and .labelSelector.matchLabels.\"app.kubernetes.io/instance\" == \"cert-manager\" and .labelSelector.matchLabels.\"app.kubernetes.io/component\" == \"${component}\")"
}

component_preflight "controller" '.spec.source.helm.valuesObject'
component_preflight "webhook" '.spec.source.helm.valuesObject.webhook'
component_preflight "cainjector" '.spec.source.helm.valuesObject.cainjector'

if [ "${FAILURES}" -ne 0 ]; then
  echo "ERROR: cert-manager GitOps preflight found ${FAILURES} missing upgrade precondition(s)." >&2
  echo "       This is a desired-state check only; do not treat it as live-cluster health." >&2
  exit 1
fi

echo "cert-manager static upgrade preflight passed."
echo "Live-state checks remain required: Ready replicas, PDB disruptionsAllowed, webhook API reachability, and Certificate Ready conditions."
