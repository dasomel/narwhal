---
name: sync-versions
description: Check and synchronize VERSIONS.md with scripts/manifests versions
---

# Sync Versions - Version Synchronization Check

Compares and synchronizes versions between VERSIONS.md and actual scripts/manifests.

## Check Targets

1. **Script files**: VERSION variables and Helm chart versions in `scripts/cluster/*.sh`
2. **GitOps manifests**: targetRevision in `gitops/apps/*.yaml`

## Tasks

1. Parse VERSIONS.md to extract version list
2. Extract versions from each script/manifest
3. Report mismatches
4. (Optional) Suggest auto-update

## Output Example

```
=== Version Sync Report ===

[OK] Cilium: v1.19.0 (VERSIONS.md) = v1.19.0 (scripts/cluster/03-cni-install.sh)
[MISMATCH] cert-manager: v1.19.3 (VERSIONS.md) != v1.19.2 (gitops/apps/cert-manager.yaml)

Found 1 mismatch(es). Fix? [y/n]
```
