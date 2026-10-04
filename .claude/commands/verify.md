---
name: verify
description: Full verification loop - Vagrantfile, scripts, YAML syntax check and cluster state inspection
---

# Verify - Full Verification Loop

Validates work results to ensure quality.

## Verification Steps

### 1. Syntax Validation
```bash
# Vagrantfile
ruby -c Vagrantfile

# Shell scripts
find scripts/ -name '*.sh' -print0 | xargs -0 shellcheck --severity=warning

# YAML files
fail=0
for f in gitops/resources/*.yaml; do
  yq eval '.' "$f" > /dev/null || { echo "FAIL: $f"; fail=1; }
done
helm template narwhal-apps gitops/charts/narwhal-apps > /dev/null || fail=1
[ "$fail" -eq 0 ]
```

### 2. Git Status Check
```bash
git status --short
git diff --stat
```

### 3. Cluster State Check (if VM is running)
```bash
# Node status
vagrant ssh master-1 -c "kubectl get nodes" 2>/dev/null || echo "VM not running"

# Pod status
vagrant ssh master-1 -c "kubectl get pods -A --field-selector=status.phase!=Running 2>/dev/null | head -20" || true

# ArgoCD app status
vagrant ssh master-1 -c "kubectl get applications -n devtools 2>/dev/null" || true
```

### 4. Version Consistency Check
- Compare VERSIONS.md with script versions
- Verify chart versions in gitops/charts/narwhal-apps/templates/*.yaml

## Output Format

```
=== Verification Report ===
[OK] Vagrantfile syntax
[OK] scripts/cluster/02-init-cluster.sh
[WARN] scripts/cluster/11-keycloak.sh - shellcheck warnings
[OK] gitops/resources/rbac-policies.yaml
[FAIL] helm template gitops/charts/narwhal-apps - render error

Cluster Status: 6/6 nodes Ready
ArgoCD Apps: 8 Synced, 1 Progressing
===========================
```

## Usage

Running this command performs all validations in order and summarizes results.
Suggests fixes for any failures found.
