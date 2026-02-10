# Narwhal Project Context Cache

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Host Machine                                 │
│                     (VirtualBox / VMware Fusion)                         │
└──────────────────────────────────────────────────────────────────────────┘
        │
        │ vagrant up
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                            Vagrant VMs                                    │
├─────────────────────┬──────────────────────┬─────────────────────────────┤
│      master         │      worker-1        │        worker-2             │
│   192.168.56.10     │   192.168.56.21      │     192.168.56.22           │
│    Control Plane    │      Worker          │        Worker               │
│  (2 CPU, 4GB RAM)   │  (2 CPU, 4GB RAM)    │    (2 CPU, 4GB RAM)         │
├─────────────────────┴──────────────────────┴─────────────────────────────┤
│                         VIP: 192.168.56.100 (kube-vip)                   │
└──────────────────────────────────────────────────────────────────────────┘
        │
        │ Kubernetes Cluster
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         Kubernetes Layer                                  │
├──────────────────────────────────────────────────────────────────────────┤
│  CNI: Cilium + Hubble     │  Storage: NFS + csi-driver-nfs              │
│  DB:  CloudNative-PG      │  IAM:     Keycloak (OIDC)                   │
│  Git: Gitea               │  GitOps:  ArgoCD                            │
└──────────────────────────────────────────────────────────────────────────┘
        │
        │ ArgoCD App-of-Apps
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        IDP Applications                                   │
├──────────────────┬──────────────────┬────────────────────────────────────┤
│    Monitoring    │     Security     │           DevTools                 │
├──────────────────┼──────────────────┼────────────────────────────────────┤
│  Prometheus      │  cert-manager    │  Harbor (Registry)                 │
│  Grafana         │  OpenBao         │  Headlamp (K8s UI)                 │
│  Loki            │  Kyverno         │  Velero (Backup)                   │
│  Tempo           │                  │  SeaweedFS (S3)                    │
└──────────────────┴──────────────────┴────────────────────────────────────┘
```

## Key Entry Points

| 카테고리 | 파일 | 용도 |
|----------|------|------|
| **진입점** | `Vagrantfile` | 클러스터 설정 및 프로비저닝 정의 |
| **버전 관리** | `VERSIONS.md` | 모든 컴포넌트 버전 |
| **스크립트** | `scripts/common/*.sh` | 공통 프로비저닝 |
| **스크립트** | `scripts/master/*.sh` | Master 노드 설정 |
| **스크립트** | `scripts/worker/*.sh` | Worker 노드 설정 |
| **GitOps** | `gitops/apps/*.yaml` | ArgoCD Application 정의 |
| **리소스** | `gitops/resources/*.yaml` | K8s 리소스 매니페스트 |
| **Helm Values** | `gitops/values/*.yaml` | Helm 차트 values |
| **문서** | `docs/KEYCLOAK-SSO.md` | SSO 설정 가이드 |
| **문서** | `docs/KUBECONFIG.md` | kubeconfig 설정 가이드 |

## Common Patterns

### 새 프로비저닝 스크립트 추가

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing Component Name ==="

# Version from VERSIONS.md
VERSION="v1.0.0"

# Check if already installed
if kubectl get deployment component-name -n namespace &>/dev/null; then
  echo "Component already installed, skipping..."
  exit 0
fi

# Install using Helm
helm repo add repo-name https://charts.example.com
helm repo update
helm install component-name repo-name/chart-name \
  --namespace namespace \
  --create-namespace \
  --version ${VERSION} \
  --wait

echo "=== Component Name installed ==="
```

### 새 ArgoCD Application 추가

```yaml
# gitops/apps/component-name.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: component-name
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://charts.example.com
    targetRevision: "1.0.0"
    chart: component-name
    helm:
      releaseName: component-name
      valuesObject:
        # values here
  destination:
    server: https://kubernetes.default.svc
    namespace: component-namespace
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### Keycloak OIDC 클라이언트 추가

```bash
# scripts/master/XX-new-app.sh 에서
KEYCLOAK_URL="http://keycloak.keycloak.svc.cluster.local"

# Create OIDC client
kubectl exec -n keycloak deploy/keycloak -- \
  /opt/keycloak/bin/kcadm.sh create clients \
  -r kubernetes \
  -s clientId=new-app \
  -s 'redirectUris=["http://new-app.local/*"]' \
  -s publicClient=false \
  -s protocol=openid-connect
```

## Database Connections

| 앱 | 클러스터 | 네임스페이스 | 접속 |
|----|----------|--------------|------|
| Keycloak | keycloak-db | keycloak | keycloak-db-rw.keycloak:5432 |
| Gitea | gitea-db | gitea | gitea-db-rw.gitea:5432 |
| Harbor | harbor-db | harbor | harbor-db-rw.harbor:5432 |

## Service Access Ports

| 서비스 | 네임스페이스 | 내부 포트 | 포트포워드 명령어 |
|--------|--------------|-----------|-------------------|
| ArgoCD | argocd | 443 | `kubectl port-forward svc/argocd-server -n argocd 8443:443` |
| Keycloak | keycloak | 80 | `kubectl port-forward svc/keycloak -n keycloak 8080:80` |
| Grafana | monitoring | 80 | `kubectl port-forward svc/prometheus-grafana -n monitoring 3000:80` |
| Gitea | gitea | 3000 | `kubectl port-forward svc/gitea-http -n gitea 3000:3000` |
| Harbor | harbor | 80 | `kubectl port-forward svc/harbor-portal -n harbor 8080:80` |
| Headlamp | headlamp | 80 | `kubectl port-forward svc/headlamp -n headlamp 4466:80` |

## File Modification Guidelines

| 수정 대상 | 연관 확인 필요 |
|-----------|----------------|
| `Vagrantfile` | 스크립트 환경변수 동기화 |
| `scripts/master/0X-*.sh` | VERSIONS.md 버전 확인 |
| `gitops/apps/*.yaml` | gitops/values/*.yaml 동기화 |
| `gitops/resources/*.yaml` | 관련 앱 YAML 확인 |
| `VERSIONS.md` | 관련 스크립트/values 업데이트 |
