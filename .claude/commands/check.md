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

2. **Shell script validation** (a missing or failing validator is a FAIL, not a skip)
   ```bash
   find scripts/ -name '*.sh' -print0 | xargs -0 shellcheck --severity=warning
   ```

3. **YAML syntax validation**
   ```bash
   fail=0
   for f in gitops/resources/*.yaml; do
     yq eval '.' "$f" > /dev/null || { echo "FAIL: $f"; fail=1; }
   done
   helm template narwhal-apps gitops/charts/narwhal-apps > /dev/null || fail=1
   [ "$fail" -eq 0 ]
   ```

4. **Version consistency check**
   - Compare VERSIONS.md versions with script versions
   - Verify chart versions in gitops/charts/narwhal-apps/templates/*.yaml

5. **Output results summary**

## Usage

Running this command performs the above validations in order and summarizes results.
