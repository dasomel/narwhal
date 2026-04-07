---
name: check
description: Quick syntax check - Vagrantfile, scripts, YAML validation
---

# Quick Check - Project State Validation

Quickly validates key project configurations and scripts.

## Tasks

1. **Vagrantfile syntax validation**
   ```bash
   ruby -c Vagrantfile
   ```

2. **Shell script validation** (if shellcheck installed)
   ```bash
   shellcheck scripts/**/*.sh 2>/dev/null || echo "shellcheck not installed"
   ```

3. **YAML syntax validation**
   ```bash
   for f in gitops/apps/*.yaml gitops/resources/*.yaml; do
     yq eval '.' "$f" > /dev/null && echo "OK: $f" || echo "FAIL: $f"
   done
   ```

4. **Version consistency check**
   - Compare VERSIONS.md versions with script versions
   - Verify chart versions in gitops/apps/*.yaml

5. **Output results summary**

## Usage

Running this command performs the above validations in order and summarizes results.
