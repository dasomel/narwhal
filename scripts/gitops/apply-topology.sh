#!/bin/bash
# Transform only a staged GitOps copy (or a temporary CNPG bootstrap manifest).
set -euo pipefail

# D-SINGLE-NODE-GITOPS: persist the profile in the same copied tree Gitea/ArgoCD read.
# Cost: single loses replica redundancy; escape hatch: rebuild with ha (byte-for-byte no-op).
# Reuse at bootstrap AND later publication so selfHeal cannot restore the HA replicas.
if [ "${NARWHAL_TOPOLOGY:-ha}" != single ]; then exit 0; fi

cnpg_single() {
  yq -i '(select(.kind == "Cluster" and .metadata.name == "narwhal-db") | .spec.instances) = 1' "$1"
}
if [ "${1}" = --cnpg ]; then
  cnpg_single "$2"
else
  tree="$1"
  if [ -f "${tree}/resources/narwhal-db.yaml" ]; then
    cnpg_single "${tree}/resources/narwhal-db.yaml"
  fi
  templates="${tree}/charts/narwhal-apps/templates"
  if [ -f "${templates}/cert-manager.yaml" ]; then
    yq -i '.spec.source.helm.valuesObject |= (.replicaCount = 1 | .webhook.replicaCount = 1 | .cainjector.replicaCount = 1)' "${templates}/cert-manager.yaml"
  fi
  if [ -f "${templates}/kyverno.yaml" ]; then
    yq -i '.spec.source.helm.valuesObject |= (.admissionController.replicas = 1 | .backgroundController.replicas = 1 | .cleanupController.replicas = 1 | .reportsController.replicas = 1)' "${templates}/kyverno.yaml"
  fi
  if [ -f "${templates}/istiod.yaml" ]; then
    yq -i '.spec.source.helm.valuesObject.replicaCount = 1' "${templates}/istiod.yaml"
  fi
fi
