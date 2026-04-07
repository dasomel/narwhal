# Narwhal Provision Patterns

Templates and patterns referenced by infra-engineer when writing scripts/YAML.

## Table of Contents
1. [Provisioning Script Template](#1-provisioning-script-template)
2. [ArgoCD Application Template](#2-argocd-application-template)
3. [K8s Resource Manifest Patterns](#3-k8s-resource-manifest-patterns)
4. [VERSIONS.md Entry Format](#4-versionsmd-entry-format)
5. [Helm Installation Patterns](#5-helm-installation-patterns)
6. [Common lib.sh Functions](#6-common-libsh-functions)

---

## 1. Provisioning Script Template

```bash
#!/bin/bash
set -euo pipefail

# =============================================================================
# XX-component-name.sh - Component description
# =============================================================================

source /home/vagrant/scripts/common/lib.sh

COMPONENT_VERSION="${COMPONENT_VERSION:-X.Y.Z}"
CHART_VERSION="${CHART_VERSION:-X.Y.Z}"
NAMESPACE="target-namespace"

echo "=========================================="
echo "Installing Component v${COMPONENT_VERSION}..."
echo "=========================================="

# --- Create namespace ---
ensure_namespace "${NAMESPACE}"

# --- (Optional) DB setup ---
# If CNPG cluster is needed, reference 07-cnpg.sh pattern

# --- (Optional) Secret creation ---
# Use generate_password for secure 24-char password generation
# Use ensure_secret_key to ensure a key exists in an existing secret
COMPONENT_PASSWORD=$(generate_password)
kubectl create secret generic "component-secrets" \
  --namespace "${NAMESPACE}" \
  --from-literal=password="${COMPONENT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# --- Add Helm repo ---
helm repo add component-repo https://charts.example.com/ || true
helm repo update component-repo

# --- Helm install ---
helm upgrade --install component component-repo/component \
  --namespace "${NAMESPACE}" \
  --version "${CHART_VERSION}" \
  --create-namespace \
  --timeout 10m \
  --values - <<'EOF'
replicaCount: 1
image:
  repository: ghcr.io/example/component
  tag: "X.Y.Z"
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 512Mi
EOF

echo "Component installed successfully."
```

### Key Rules
- `set -euo pipefail` mandatory
- `source lib.sh` mandatory (shared functions)
- Heredoc values: `<<'EOF'` (prevent variable expansion)
- `helm upgrade --install` (idempotent)
- Include `--create-namespace`
- Specify `--timeout` (default 5m-10m)
- `|| true` pattern: for helm repo add, grep, etc. where failure is acceptable

---

## 2. ArgoCD Application Template

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: component-name
  namespace: devtools
  annotations:
    argocd.argoproj.io/sync-wave: "3"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://charts.example.com/
    targetRevision: "X.Y.Z"
    chart: component-name
    helm:
      valuesObject:
        replicaCount: 1
        image:
          repository: ghcr.io/example/component
          tag: "X.Y.Z"
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            memory: 512Mi
  destination:
    server: https://kubernetes.default.svc
    namespace: target-namespace
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

### Key Rules
- `metadata.namespace: devtools` (ArgoCD namespace)
- `spec.destination.namespace`: actual deployment namespace
- `sync-wave`: dependency ordering (lower = first)
- `ServerSideApply=true`: prevents CRD 262KB overflow
- `targetRevision`: string-format chart version

---

## 3. K8s Resource Manifest Patterns

Additional resources managed via GitOps (gitops/resources/):

```yaml
# ExternalName service (cross-namespace routing)
apiVersion: v1
kind: Service
metadata:
  name: component-external
  namespace: platform-system
spec:
  type: ExternalName
  externalName: component.target-namespace.svc.cluster.local
  ports:
    - port: 8080
      targetPort: 8080
```

---

## 4. VERSIONS.md Entry Format

```markdown
| Component Name | vX.Y.Z | chart X.Y.Z | notes |
```

Grouped by category: Container Runtime, Networking, Service Mesh, Storage, Database, IAM/SSO, Security, Backup, Observability, Git/GitOps, Registry

---

## 5. Helm Installation Patterns

### CRD-First Pattern (Operators)
```bash
# Install CRDs first
kubectl apply -f https://example.com/crds.yaml --server-side --force-conflicts

# Then Helm (skip-crds)
helm upgrade --install component repo/chart \
  --namespace "${NAMESPACE}" \
  --skip-crds \
  ...
```

### Separate Values File Pattern (complex configs)
```bash
cat > /tmp/component-values.yaml <<'EOF'
# ... long values ...
EOF

helm upgrade --install component repo/chart \
  --namespace "${NAMESPACE}" \
  -f /tmp/component-values.yaml
```

### --set Caveats
- Prefer values files over `--set` (avoids type errors)
- Boolean nodeSelector: `--set-string nodeSelector.key=true`

---

## 6. Common lib.sh Functions

| Function | Purpose | Example |
|----------|---------|---------|
| `generate_password` | Generate 24-char URL-safe random password (no args) | `pw=$(generate_password)` |
| `wait_for_api_server [N]` | Wait for API server readiness (default 30 attempts) | `wait_for_api_server` or `wait_for_api_server 60` |
| `ensure_namespace NS` | Create namespace idempotently | `ensure_namespace "monitoring"` |
| `ensure_secret_key NS SECRET KEY` | Patch a key into existing secret (generates random value if key absent) | `ensure_secret_key "iam" "auth-secret" "api-key"` |
