#!/usr/bin/env bash
set -euo pipefail
FAIL=0
value() { grep -oP "$1" "$2" 2>/dev/null | head -1 || true; }
check_pair() {
  local name="$1" expected="$2" actual="$3" source="$4"
  echo "[$name]"
  echo "  VERSIONS.md : ${expected:-not found}"
  echo "  $source : ${actual:-not found}"
  if [[ -n "$actual" && -n "$expected" && "$expected" != "$actual" ]]; then
    echo "  ERROR: mismatch"; FAIL=1
  elif [[ -z "$actual" ]]; then
    echo "  SKIP: pinned source not found"
  else
    echo "  OK"
  fi
  echo
}

echo "=== Version Sync Check ==="
K8S=$(value 'Kubernetes\s*\|\s*v\K[0-9.]+' VERSIONS.md)
VAGRANT_K8S=$(value "K8S_PATCH_VERSION\s*=\s*['\"]?\K[0-9.]+" Vagrantfile)
check_pair Kubernetes "$K8S" "$VAGRANT_K8S" Vagrantfile
CILIUM=$(value 'Cilium\s*\|\s*v\K[0-9.]+' VERSIONS.md)
SCRIPT_CILIUM=$(value 'CILIUM_VERSION[=:]\s*["\x27]?v?\K[0-9.]+' scripts/cluster/03-cni-install.sh)
check_pair Cilium "$CILIUM" "$SCRIPT_CILIUM" scripts/cluster/03-cni-install.sh
ARGOCD=$(value 'ArgoCD\s*\|\s*v\K[0-9.]+' VERSIONS.md)
SCRIPT_ARGOCD=$(value 'ARGOCD_VERSION[=:]\s*["\x27]?v?\K[0-9.]+' scripts/cluster/13-argocd.sh)
check_pair ArgoCD "$ARGOCD" "$SCRIPT_ARGOCD" scripts/cluster/13-argocd.sh
KEYCLOAK=$(value 'Keycloak\s*\|\s*v\K[0-9.]+' VERSIONS.md)
SCRIPT_KEYCLOAK=$(value 'KEYCLOAK_VERSION[=:]\s*["\x27]?v?\K[0-9.]+' scripts/cluster/11-keycloak.sh)
check_pair Keycloak "$KEYCLOAK" "$SCRIPT_KEYCLOAK" scripts/cluster/11-keycloak.sh
TRAEFIK=$(value 'Traefik\s*\|\s*v\K[0-9.]+' VERSIONS.md)
GITOPS_TRAEFIK=$(value 'targetRevision:\s*\K[0-9.]+' gitops/apps/traefik.yaml)
check_pair 'Traefik chart' "$TRAEFIK" "$GITOPS_TRAEFIK" gitops/apps/traefik.yaml

echo "=== Summary ==="
if (( FAIL )); then
  echo "One or more version mismatches detected."
  echo "Update VERSIONS.md or the pinned runtime source so both describe the same release."
  exit 1
fi
echo "All checked versions are consistent."
