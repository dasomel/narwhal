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
shellcheck scripts/**/*.sh 2>/dev/null || echo "shellcheck not installed"

# YAML files
for f in gitops/apps/*.yaml gitops/resources/*.yaml; do
  yq eval '.' "$f" > /dev/null 2>&1 && echo "OK: $f" || echo "FAIL: $f"
done
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
- Verify chart versions in gitops/apps/*.yaml

## Output Format

```
=== Verification Report ===
[OK] Vagrantfile syntax
[OK] scripts/cluster/02-init-cluster.sh
[WARN] scripts/cluster/11-authentik.sh - shellcheck warnings
[OK] gitops/apps/cert-manager.yaml
[FAIL] gitops/apps/harbor.yaml - YAML syntax error

Cluster Status: 3/3 nodes Ready
ArgoCD Apps: 8 Synced, 1 Progressing
===========================
```

## Usage

Running this command performs all validations in order and summarizes results.
Suggests fixes for any failures found.
