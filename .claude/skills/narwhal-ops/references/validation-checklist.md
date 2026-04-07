# Narwhal Validation Checklist

Detailed checklist referenced by infra-validator during validation.

## Table of Contents
1. [Shell Script Validation](#1-shell-script-validation)
2. [YAML Validation](#2-yaml-validation)
3. [Version Consistency Validation](#3-version-consistency-validation)
4. [Security Validation](#4-security-validation)
5. [Mistakes Log Cross-Reference Key Patterns](#5-mistakes-log-cross-reference-key-patterns)
6. [Cluster State Validation](#6-cluster-state-validation)

---

## 1. Shell Script Validation

### Automated (shellcheck)
```bash
shellcheck scripts/cluster/*.sh scripts/common/*.sh
```

### Manual Checklist

| Item | How to Check | PASS Criteria |
|------|-------------|---------------|
| set -euo pipefail | Within first 3 lines | Present |
| source lib.sh | grep "source.*lib.sh" | Present (Phase 2 scripts) |
| apt -y flag | grep "apt-get install" | -y on all installs |
| Heredoc quoting | grep "<<EOF" vs "<<'EOF'" | Matches variable expansion intent |
| grep pipefail safety | grep pipelines | `\|\| true` or variable assignment |
| Variable quoting | shellcheck SC2086 | `"$VAR"` format |
| Function usage | generate_password, ensure_namespace, etc. | Uses lib.sh functions |

---

## 2. YAML Validation

### Automated
```bash
for f in gitops/apps/*.yaml gitops/resources/*.yaml; do
  yq eval '.' "$f" > /dev/null 2>&1 || echo "FAIL: $f"
done
```

### ArgoCD Application Required Fields

| Field | Required | Check |
|-------|----------|-------|
| metadata.name | Y | Unique value |
| metadata.namespace | Y | `devtools` |
| spec.project | Y | `default` |
| spec.source.repoURL | Y | Valid URL |
| spec.source.targetRevision | Y | String version |
| spec.destination.server | Y | `https://kubernetes.default.svc` |
| spec.destination.namespace | Y | Actual deployment NS |
| spec.syncPolicy.syncOptions | Y | Includes `CreateNamespace=true` |

### app-of-apps Reference Check
Verify new app is included in `gitops/apps/app-of-apps.yaml`.

---

## 3. Version Consistency Validation

Versions must match across 3 locations:

```
VERSIONS.md  <-->  scripts/cluster/*.sh  <-->  gitops/apps/*.yaml
```

### How to Verify
1. Extract each component's chart version and app version from VERSIONS.md
2. Grep `--version`, `--set image.tag`, `CHART_VERSION` in corresponding script
3. Grep `targetRevision`, `image.tag` in corresponding GitOps YAML
4. Compare all three values
5. On mismatch: report file:line + both values

---

## 4. Security Validation

### Secret Detection

```bash
# Hardcoded password patterns
grep -rn "password\s*[:=]\s*['\"]" scripts/ gitops/ --include="*.sh" --include="*.yaml"
# Base64-encoded secrets
grep -rn "base64" gitops/ --include="*.yaml"
# API keys/tokens
grep -rn "token\|api[_-]key\|secret[_-]key" scripts/ gitops/ --include="*.sh" --include="*.yaml"
```

**Allowed exceptions:**
- `generate_password` function calls (dynamic generation)
- kubectl get secret (retrieval)
- Environment variable references (`${VAR}`)

### Image Source Validation

| Registry | Verdict |
|----------|---------|
| ghcr.io | PASS |
| registry.k8s.io | PASS |
| quay.io | PASS |
| docker.io (with justification comment) | WARN |
| docker.io (no justification) | FAIL |
| Bitnami (docker.io/bitnami/*) | FAIL (banned) |

### Privilege Validation

| Item | Detection | Verdict |
|------|-----------|---------|
| privileged: true | grep | FAIL (unless justified) |
| hostNetwork: true | grep | WARN |
| hostPID: true | grep | FAIL |
| runAsRoot | grep | WARN |

---

## 5. Mistakes Log Cross-Reference Key Patterns

Most frequent mistake patterns from CLAUDE.md (priority checks for new code):

| Pattern | Check Point |
|---------|-------------|
| Helm --wait timeout | Whether --wait is used on non-critical apps |
| CRD 262KB overflow | ServerSideApply=true on large CRD apps |
| Bitnami images | Whether image source is bitnami |
| CNPG secret GitOps overwrite | narwhal-db-credentials NOT in GitOps YAML |
| etcd auth error | APISIX externalEtcd.user default "root" check |
| Istio ambient cookie corruption | dataplane-mode: none on SSO web servers |
| CoreDNS loop | forward . /etc/resolv.conf presence |
| Image tag v prefix | alpine/k8s, velero-ui v prefix mismatch |
| Keycloak OIDC audience | All Keycloak clients have `oidc-audience-mapper` configured |
| Keycloak exec method | Use `kubectl exec -n iam keycloak-0 -c keycloak` (StatefulSet, not Deployment) |
| APISIX IC ExternalName routes | Routes with ExternalName backends must be in admin API scripts, not ApisixRoute YAML |
| APISIX openid-connect plugin | SSO routes use per-route OIDC plugin (NOT ForwardAuth) |
| APISIX admin API access | `allow_admin: [0.0.0.0/0]` in config (default blocks) |

---

## 6. Cluster State Validation (when VM is running)

```bash
# Node status
vagrant ssh master-1 -c "kubectl get nodes"
# All unhealthy pods
vagrant ssh master-1 -c "kubectl get pods -A | grep -v Running | grep -v Completed"
# ArgoCD sync status
vagrant ssh master-1 -c "kubectl get applications -n devtools"
# Resource usage
vagrant ssh master-1 -c "kubectl top nodes"
```
