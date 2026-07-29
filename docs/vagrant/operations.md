# Narwhal 운영 가이드

> Vagrant 기반 Kubernetes IDP 클러스터의 일상 운영 절차 및 관리 작업

## 1. 클러스터 시작/중지/재시작/삭제

### 클러스터 시작

```bash
# 전체 클러스터 시작 (최초 프로비저닝 포함)
vagrant up --provider=vmware_desktop

# 특정 노드만 시작
vagrant up master-1
vagrant up worker-1

# 여러 노드 동시 시작
vagrant up master-1 master-2 master-3 worker-1
```

### 클러스터 중지

```bash
# 전체 클러스터 중지 (VM 상태 보존)
vagrant halt

# 특정 노드만 중지
vagrant halt worker-3

# 모든 worker만 중지
vagrant halt worker-1 worker-2 worker-3
```

### 클러스터 재시작

```bash
# 전체 클러스터 재시작
vagrant halt && vagrant up

# 특정 노드 재시작
vagrant halt master-1 && vagrant up master-1
```

### 클러스터 삭제

```bash
# 전체 클러스터 완전 삭제 (VM 및 데이터 삭제)
vagrant destroy -f

# 특정 노드만 삭제
vagrant destroy worker-2 -f
```

### SSH 접속

```bash
# Master 노드 접속
vagrant ssh master-1

# Worker 노드 접속
vagrant ssh worker-1

# 명령어 직접 실행
vagrant ssh master-1 -c "kubectl get nodes"
```

**주의사항**:
- `vagrant up` 후 모든 노드가 Ready가 될 때까지 2-3분 대기
- master-1이 먼저 기동되어야 다른 노드가 API 서버에 접근 가능
- VM 삭제 후 재생성 시 NFS 데이터도 삭제됨 (백업 필수)
- OpenBao는 VM 재시작 시마다 unseal 필요

---

## 2. Phase 2 수동 실행

Phase 2 플랫폼 앱은 마지막 worker 조인 후 자동 트리거되지만, 수동으로도 실행 가능합니다.

### Phase 2 전체 실행

```bash
# Vagrant provisioner로 실행
vagrant provision master-1 --provision-with phase2-platform
```

### 개별 스크립트 실행

VM 내에서 각 스크립트를 순서대로 실행:

```bash
# PostgreSQL Operator
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/07-cnpg.sh"

# 플랫폼 앱 (cert-manager, APISIX, MetalLB 등) - 분해된 스크립트 순차 실행
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-1-networking.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-2-monitoring.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-3-security.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-4-storage.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-5-registry.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-6-tls-routes.sh"

# Istio Ambient Mesh
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/09-istio-ambient.sh"

# dnsmasq (로컬 DNS)
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/10-dnsmasq.sh"

# Keycloak (SSO/OIDC) - 4단계 순차 실행
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-1-keycloak-operator.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-2-keycloak-realm.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-3-keycloak-clients.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-4-keycloak-apiserver.sh"

# Gitea (Git 서버)
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/12-gitea.sh"

# ArgoCD (GitOps)
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/13-argocd.sh"

# GitOps Bootstrap (App-of-Apps)
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/14-gitops-bootstrap.sh"
```

**실행 순서 중요**:
- cert-manager/APISIX (08) → Istio (09) → DNS (10) → Keycloak OIDC (11) 순서를 반드시 지켜야 함
- HTTPS 인증서가 준비된 후에 OIDC 설정이 활성화됨 (K8s 1.35+)

### Istio Ambient Mesh 운영

```bash
# Istio 컴포넌트 확인
vagrant ssh master-1 -c "kubectl get pods -n istio-system"

# mTLS 정책 확인
vagrant ssh master-1 -c "kubectl get peerauthentication -A"

# ambient 네임스페이스 확인
vagrant ssh master-1 -c "kubectl get ns -L istio.io/dataplane-mode"

# 네임스페이스에 ambient 라벨 추가/제거
vagrant ssh master-1 -c "kubectl label ns <namespace> istio.io/dataplane-mode=ambient"
vagrant ssh master-1 -c "kubectl label ns <namespace> istio.io/dataplane-mode-"

# Cilium-Istio 공존 설정 확인
vagrant ssh master-1 -c "kubectl get cm cilium-config -n kube-system -o yaml | grep -E 'cni-exclusive|socket-lb-host-ns-only'"

# ztunnel 로그 (mTLS 핸드셰이크 확인)
vagrant ssh master-1 -c "kubectl logs -n istio-system -l app=ztunnel --tail=50"
```

**스크립트 수정 후 재실행**:
```bash
# 로컬에서 스크립트 수정 후
vagrant rsync master-1
vagrant provision master-1 --provision-with phase2-platform
```

---

## 3. OpenBao 초기화 및 Unseal

OpenBao는 설치 후 수동 초기화 및 unseal이 필요합니다.

### 최초 초기화

```bash
vagrant ssh master-1

# OpenBao Pod 확인
kubectl get pods -n openbao

# 초기화 (최초 1회만)
kubectl exec -n openbao openbao-0 -- bao operator init \
  -key-shares=1 \
  -key-threshold=1

# 출력 예시:
# Unseal Key 1: <unseal-key>
# Initial Root Token: <root-token>
#
# ⚠️ 이 값을 안전한 곳에 보관하세요!
```

### Unseal 실행

```bash
# VM 재시작 후 매번 실행 필요
kubectl exec -n openbao openbao-0 -- bao operator unseal <unseal-key>
```

### 상태 확인

```bash
# Sealed 여부 확인
kubectl exec -n openbao openbao-0 -- bao status

# 출력 예시:
# Sealed: false  ← unsealed 상태
# Key Shares: 1
# Key Threshold: 1
```

### UI 접속

```bash
# OpenBao UI는 HTTPRoute로 노출됨
# https://openbao.local.narwhal.internal
# Root Token으로 로그인
```

**주의사항**:
- VM 재시작 시마다 unseal 필요 (sealed 상태로 시작)
- Unseal Key와 Root Token은 안전하게 보관 (분실 시 복구 불가)
- 프로덕션 환경에서는 auto-unseal 설정 권장

---

## 4. GitOps 앱 추가/제거

### 앱 추가 절차

#### 1. GitOps 파일 생성

```bash
# ArgoCD Application 정의 (narwhal-apps Helm 차트의 템플릿으로 추가)
cat > gitops/charts/narwhal-apps/templates/my-app.yaml << 'EOF'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://charts.example.com
    chart: my-app
    targetRevision: "1.0.0"
    helm:
      valueFiles:
        - values.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: my-namespace
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
EOF

# Helm values 파일 (필요 시)
## ArgoCD Application spec.source.helm.values에 inline으로 values를 추가하세요.
## 별도의 values 파일 대신 Application YAML에 직접 포함합니다.
```

#### 2. App-of-Apps 자동 인식 확인

`app-of-apps.yaml`은 `path: charts/narwhal-apps`(Helm 차트)를 가리키므로 `templates/`
아래 새 파일을 추가하는 것만으로 자동으로 렌더링/적용됩니다. 별도의 include 목록에
등록하는 절차는 필요 없습니다 (과거 raw-manifest + `directory.include` 방식에서
Helm 차트 방식으로 전환됨).

#### 3. Gitea 리포지토리에 push

```bash
vagrant ssh master-1 -c "cd /home/vagrant/narwhal-gitops && \
  git add gitops/charts/narwhal-apps/templates/my-app.yaml && \
  git commit -m 'Add my-app application' && \
  git push"
```

#### 4. ArgoCD 동기화 확인

```bash
# Application 목록 확인
vagrant ssh master-1 -c "kubectl get applications -n argocd"

# 특정 앱 상태 확인
vagrant ssh master-1 -c "kubectl get application my-app -n argocd -o yaml"

# ArgoCD UI에서 확인
# https://argocd.local.narwhal.internal
```

### 앱 제거 절차

```bash
# 방법 1: ArgoCD에서 직접 삭제 (cascade로 리소스도 삭제)
vagrant ssh master-1 -c "kubectl delete application my-app -n argocd"

# 방법 2: GitOps에서 제거 후 동기화
# gitops/charts/narwhal-apps/templates/my-app.yaml 삭제
# Gitea에 push (Helm 템플릿이 사라지면 ArgoCD prune이 리소스를 자동 제거)
```

**주의사항**:
- ArgoCD Application 삭제 시 `finalizers`로 인해 관리 중인 리소스도 함께 삭제됨
- 리소스만 보존하려면 Application에서 `finalizers` 제거 후 삭제
- GitOps 파일 삭제만으로는 ArgoCD에서 자동 제거되지 않음

---

## 5. 컴포넌트 업그레이드

### Helm 차트 직접 업그레이드

```bash
# 1. 현재 릴리스 목록 확인
vagrant ssh master-1 -c "helm list -A"

# 2. 차트 버전 확인
vagrant ssh master-1 -c "helm search repo <chart-name> --versions"

# 3. breaking changes 확인 (중요!)
# 차트 공식 문서에서 CHANGELOG 확인

# 4. values 파일 준비
cat > /tmp/new-values.yaml << 'EOF'
# 새 버전 설정
EOF

# 5. 업그레이드 실행
vagrant ssh master-1 -c "helm upgrade <release-name> <chart> \
  -n <namespace> \
  -f /tmp/new-values.yaml \
  --version <new-version>"

# 6. 롤백 (문제 발생 시)
vagrant ssh master-1 -c "helm rollback <release-name> -n <namespace>"
```

### ArgoCD 관리 앱 업그레이드

GitOps values 파일에서 버전을 변경하고 Gitea에 push하면 ArgoCD가 자동 동기화합니다.

```bash
# 1. GitOps 파일 수정
# gitops/charts/narwhal-apps/templates/my-app.yaml에서 targetRevision 변경
spec:
  source:
    targetRevision: "2.0.0"  # 버전 업데이트

# 2. Gitea에 push
vagrant ssh master-1 -c "cd /home/vagrant/narwhal-gitops && \
  git add gitops/charts/narwhal-apps/templates/my-app.yaml && \
  git commit -m 'Upgrade my-app to 2.0.0' && \
  git push"

# 3. ArgoCD 동기화 대기 (자동) 또는 수동 트리거
vagrant ssh master-1 -c "kubectl patch application my-app -n argocd \
  --type merge -p '{\"operation\":{\"initiatedBy\":{\"username\":\"admin\"}}}'"
```

### Kubernetes 버전 업그레이드

kubeadm 기반 클러스터이므로 공식 절차를 따릅니다.

```bash
# 1. Master 노드 업그레이드
vagrant ssh master-1

# 업그레이드 계획 확인
sudo kubeadm upgrade plan

# 업그레이드 적용
sudo kubeadm upgrade apply v1.36.0

# kubelet 업그레이드
sudo apt-mark unhold kubelet kubectl
sudo apt-get update && sudo apt-get install -y kubelet=1.36.0-* kubectl=1.36.0-*
sudo apt-mark hold kubelet kubectl
sudo systemctl daemon-reload
sudo systemctl restart kubelet

# 2. Worker 노드 업그레이드 (각 노드마다)
vagrant ssh master-1 -c "kubectl drain worker-1 --ignore-daemonsets"
vagrant ssh worker-1

sudo kubeadm upgrade node
sudo apt-mark unhold kubelet kubectl
sudo apt-get update && sudo apt-get install -y kubelet=1.36.0-* kubectl=1.36.0-*
sudo apt-mark hold kubelet kubectl
sudo systemctl daemon-reload
sudo systemctl restart kubelet

exit
vagrant ssh master-1 -c "kubectl uncordon worker-1"

# 3. 노드 상태 확인
vagrant ssh master-1 -c "kubectl get nodes"
```

### VERSIONS.md 동기화

업그레이드 후 반드시 `VERSIONS.md` 파일을 실제 배포 버전과 동기화하세요.

```bash
# 현재 배포된 버전 확인
vagrant ssh master-1 -c "helm list -A"
vagrant ssh master-1 -c "kubectl version --short"

# VERSIONS.md 업데이트 후 커밋
git add VERSIONS.md
git commit -m "docs: update VERSIONS.md after upgrade"
```

---

## 6. Worker 노드 추가

### Vagrantfile 수정

```ruby
# Vagrantfile에서 WORKER_COUNT 증가
WORKER_COUNT = 3  # 2에서 3으로 증가

# 또는 새 노드 직접 정의
(1..WORKER_COUNT).each do |i|
  config.vm.define "worker-#{i}" do |worker|
    worker.vm.hostname = "worker-#{i}"
    worker.vm.network "private_network", ip: "192.168.56.#{20 + i}"
    # ...
  end
end
```

### 노드 프로비저닝

```bash
# 새 worker 노드 시작
vagrant up worker-3

# 자동으로 kubeadm join 실행됨
# join 토큰은 master-1의 /etc/narwhal/join-command에서 읽음
```

### 노드 확인

```bash
# 노드 목록 확인
vagrant ssh master-1 -c "kubectl get nodes"

# 새 노드 상태 확인
vagrant ssh master-1 -c "kubectl describe node worker-3"

# Pod 재분배 확인
vagrant ssh master-1 -c "kubectl get pods -A -o wide | grep worker-3"
```

**IP 대역 규칙**:
- Master: `192.168.56.10` (master-1), `192.168.56.11` (master-2), `192.168.56.12` (master-3)
- Worker: `192.168.56.2X` (worker-1: .21, worker-2: .22, worker-3: .23)
- VIP: `192.168.56.100`
- MetalLB: `192.168.56.200`

**리소스 할당**:
- Master CPU: 2 cores, Memory: 4GB (control-plane only, NoSchedule taint)
- Worker CPU: 2 cores (Vagrantfile에서 조정 가능)
- Worker Memory: 6GB (Vagrantfile에서 조정 가능)

---

## 7. PostgreSQL Failover (CNPG)

CloudNative-PG Operator가 자동으로 failover를 처리하지만, 수동 작업이 필요한 경우도 있습니다.

### 클러스터 상태 확인

```bash
# CNPG 클러스터 목록
vagrant ssh master-1 -c "kubectl get cluster -n database"

# 특정 클러스터 상태 (예: Harbor DB)
vagrant ssh master-1 -c "kubectl get cluster harbor-db -n harbor -o yaml"

# 인스턴스 Pod 상태
vagrant ssh master-1 -c "kubectl get pods -n database -l cnpg.io/cluster=narwhal-db"

# Primary 확인
vagrant ssh master-1 -c "kubectl get cluster narwhal-db -n database \
  -o jsonpath='{.status.currentPrimary}'"
```

### PgBouncer 상태 확인

```bash
# PgBouncer Pooler 확인
vagrant ssh master-1 -c "kubectl get pooler -n database"

# PgBouncer Pod 확인
vagrant ssh master-1 -c "kubectl get pods -n database -l cnpg.io/poolerName"

# PgBouncer 로그
vagrant ssh master-1 -c "kubectl logs -n database \
  -l cnpg.io/poolerName=narwhal-db-rw -f"
```

### 수동 Failover

```bash
# Primary를 특정 인스턴스로 변경
vagrant ssh master-1 -c "kubectl cnpg promote narwhal-db narwhal-db-2 -n database"

# Failover 진행 확인
vagrant ssh master-1 -c "kubectl get cluster narwhal-db -n database -w"
```

### 장애 복구 시나리오

#### Replica PVC 문제

```bash
# 문제 있는 PVC 확인
vagrant ssh master-1 -c "kubectl get pvc -n database"

# 문제 있는 replica Pod 삭제
vagrant ssh master-1 -c "kubectl delete pod narwhal-db-2 -n database"

# PVC 삭제 (주의: 데이터 손실)
vagrant ssh master-1 -c "kubectl delete pvc narwhal-db-2 -n database"

# CNPG가 자동으로 새 PVC 생성 및 WAL replay로 복구
```

#### WAL 아카이빙 실패

```bash
# 클러스터 스펙에서 인스턴스 수 줄이기
vagrant ssh master-1 -c "kubectl edit cluster narwhal-db -n database"
# spec.instances: 3 → 1로 변경

# 안정화 후 다시 증가
# spec.instances: 1 → 2로 변경
```

#### 연결 문제

```bash
# PgBouncer 재시작
vagrant ssh master-1 -c "kubectl rollout restart deployment \
  -n database -l cnpg.io/poolerName=narwhal-db-rw"

# Primary Pod 재시작 (최후 수단)
vagrant ssh master-1 -c "kubectl delete pod narwhal-db-1 -n database"
```

**주의사항**:
- Primary Pod 삭제 시 자동 failover 발생 (짧은 downtime)
- PVC 삭제 전 데이터 백업 필수
- 프로덕션 환경에서는 `instances: 3` 권장 (HA)

---

## 8. 인증서 관리

cert-manager가 self-signed CA로 wildcard 인증서를 자동 관리합니다.

### 인증서 목록 확인

```bash
# 전체 namespace의 인증서
vagrant ssh master-1 -c "kubectl get certificates -A"

# cert-manager namespace
vagrant ssh master-1 -c "kubectl get certificates -n cert-manager"
```

### 인증서 상태 확인

```bash
# 특정 인증서 상태
vagrant ssh master-1 -c "kubectl describe certificate narwhal-wildcard-cert \
  -n cert-manager"

# 인증서 Ready 상태 확인
vagrant ssh master-1 -c "kubectl get certificate narwhal-wildcard-cert \
  -n cert-manager -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}'"
```

### CA 인증서 확인

```bash
# CA cert Secret 조회
vagrant ssh master-1 -c "kubectl get secret narwhal-ca-cert -n cert-manager \
  -o jsonpath='{.data.ca\.crt}' | base64 -d | openssl x509 -text -noout"

# CA cert 파일로 저장
vagrant ssh master-1 -c "kubectl get secret narwhal-ca-cert -n cert-manager \
  -o jsonpath='{.data.ca\.crt}' | base64 -d > /tmp/narwhal-ca.crt"
```

### 인증서 수동 갱신

만료 전 자동 갱신되지만, 수동으로도 가능합니다.

```bash
# TLS Secret 삭제 (cert-manager가 자동 재생성)
vagrant ssh master-1 -c "kubectl delete secret narwhal-wildcard-tls -n cert-manager"

# Certificate 리소스 삭제 후 재생성
vagrant ssh master-1 -c "kubectl delete certificate narwhal-wildcard-cert \
  -n cert-manager"
# ArgoCD가 자동으로 Certificate 재생성
```

### CA cert 배포 확인

각 앱 namespace에 `narwhal-ca-cert` Secret이 복사되어 있어야 합니다.

```bash
# 앱별 CA cert Secret 확인
vagrant ssh master-1 -c "kubectl get secret narwhal-ca-cert -n headlamp"
vagrant ssh master-1 -c "kubectl get secret narwhal-ca-cert -n gitea"
vagrant ssh master-1 -c "kubectl get secret narwhal-ca-cert -n harbor"
vagrant ssh master-1 -c "kubectl get secret narwhal-ca-cert -n monitoring"

# Pod에 마운트 확인 (예: Headlamp)
vagrant ssh master-1 -c "kubectl exec -n headlamp \
  deployment/headlamp -- ls -l /etc/ssl/certs/narwhal-ca.crt"
```

**CA cert 마운트 패턴**:
- `volumes` + `volumeMounts` (subPath)로 `/etc/ssl/certs/narwhal-ca.crt` 마운트
- Go 앱은 자동으로 `/etc/ssl/certs/` 디렉토리 스캔하여 인식
- `SSL_CERT_FILE` 환경 변수 설정 금지 (시스템 CA 번들 대체됨)

---

## 9. 백업 확인 및 복구 (Velero)

Velero는 Kubernetes 리소스 및 Persistent Volume 백업을 담당합니다.

### 백업 목록 확인

```bash
# 백업 목록
vagrant ssh master-1 -c "kubectl get backups -n velero"

# 백업 상세 정보
vagrant ssh master-1 -c "kubectl describe backup <backup-name> -n velero"
```

### 스케줄 백업 확인

```bash
# 백업 스케줄 목록
vagrant ssh master-1 -c "kubectl get schedules -n velero"

# 스케줄 상세 정보
vagrant ssh master-1 -c "kubectl describe schedule daily-backup -n velero"
```

### 수동 백업 생성

```bash
# 전체 클러스터 백업
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup create manual-full-backup"

# 특정 namespace만 백업
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup create manual-gitea-backup --include-namespaces=gitea"

# 여러 namespace 백업
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup create manual-apps-backup \
  --include-namespaces=gitea,keycloak,harbor"

# Label selector로 백업
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup create manual-labeled-backup \
  --selector='app=my-app'"
```

### 백업 상태 확인

```bash
# 백업 진행 상황
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup describe <backup-name>"

# 백업 로그 확인
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup logs <backup-name>"

# 백업 파일 위치 확인 (NFS)
vagrant ssh master-1 -c "ls -lh /nfs/velero/backups/"
```

### 복구 (Restore)

```bash
# 백업에서 복구
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero restore create --from-backup <backup-name>"

# 특정 namespace만 복구
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero restore create restore-gitea \
  --from-backup <backup-name> \
  --include-namespaces=gitea"

# 복구 상태 확인
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero restore describe <restore-name>"

# 복구 로그 확인
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero restore logs <restore-name>"
```

### 백업 삭제

```bash
# 백업 삭제 (스토리지에서도 삭제)
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup delete <backup-name>"

# 오래된 백업 자동 삭제 (30일 이상)
vagrant ssh master-1 -c "kubectl exec -n velero deployment/velero -- \
  velero backup delete --older-than 720h"
```

**주의사항**:
- Velero는 `upgradeCRDs: false` 설정 필수 (musl/glibc 비호환)
- NFS 기반 백업 스토리지 사용 (`/nfs/velero/`)
- PVC 백업은 restic 통합 사용 (설정에 따라)
- 복구 시 기존 리소스와 충돌 가능 (namespace 삭제 후 복구 권장)

---

## 10. 검증 스크립트 사용법

통합 검증 스크립트로 클러스터 상태를 종합적으로 확인할 수 있습니다.

### 전체 검증

```bash
# 전체 검증 실행 (17개 섹션)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh"
```

**검증 섹션**:
1. Nodes
2. kube-vip & VIP
3. etcd
4. Cilium CNI
5. Core Services (CoreDNS, metrics-server, NFS)
6. Database (CNPG)
7. MetalLB & APISIX
8. cert-manager & TLS
9. DNS (dnsmasq)
10. Keycloak & OIDC
11. Monitoring (Prometheus, Grafana, Loki, Tempo)
12. Platform Apps (Kyverno, Headlamp, SeaweedFS, Harbor, OpenBao, Velero)
13. GitOps (Gitea, ArgoCD)
14. Gateway Routes (ApisixRoutes, HTTPS connectivity)
15. Istio Ambient Mesh (istiod, istio-cni, ztunnel, PeerAuthentication, ambient NS)
16. Pod Health (Pending, CrashLoopBackOff, Helm releases, ArgoCD apps)
17. Problem Pods (global check)

### 단계별 검증

```bash
# Phase 1만 검증 (클러스터 인프라)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh \
  --stage=phase1"

# Phase 2 인프라만 검증 (cert-manager, APISIX, DNS)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh \
  --stage=phase2-infra"

# Phase 2 앱만 검증 (Keycloak, Gitea, ArgoCD)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh \
  --stage=phase2-apps"
```

### 빠른 검증

```bash
# Pod DNS 테스트 스킵 (시간 절약)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh \
  --quick"
```

### SSO 통합 테스트

```bash
# SSO 전체 테스트 (7개 섹션, 49 checks)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh"
```

**SSO 테스트 섹션**:
1. Keycloak Client Scopes
2. Keycloak Client Scope Mappers
3. Client Scope Assignments
4. Token Flows
5. TLS Skip-Verify Settings
6. OIDC Configurations
7. OIDC Endpoints

### 특정 섹션만 테스트

```bash
# TLS 설정만 확인
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh \
  --section=tls"

# Token flow만 확인
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh \
  --section=tokens"

# OIDC 설정만 확인
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh \
  --section=oidc"
```

### 검증 결과 해석

```bash
# 성공 예시
✓ All nodes are Ready
✓ kube-vip pod is running
✓ VIP 192.168.56.100 is reachable
✓ Cilium pods are running
✓ cert-manager webhook is ready
✓ APISIX gateway is ready
# ...
✓ ALL 120+ CHECKS PASSED

# 실패 예시
✗ Keycloak pod is not ready
  → kubectl get pods -n keycloak
  → kubectl describe pod <pod-name> -n keycloak
```

**트러블슈팅**:
- 실패한 체크 항목은 `✗` 마크와 함께 힌트 명령어 표시
- 검증 스크립트 로그는 `/tmp/verify-cluster.log`에 저장
- `set -euo pipefail`로 첫 에러에서 중단되지 않음 (모든 체크 실행)

---

## 관련 문서

- [architecture.md](../common/architecture.md) - 아키텍처 개요
- [dns-access.md](dns-access.md) - 서비스 URL, SSO 로그인, 기본 자격 증명
- [dns-access.md](dns-access.md) - DNS 및 접근 방법
- [database.md](../common/database.md) - 데이터베이스 관리
- [troubleshooting.md](../common/troubleshooting.md) - 트러블슈팅 가이드
- [../VERSIONS.md](../../VERSIONS.md) - 컴포넌트 버전 관리
- [../README.md](../../README.md) - 프로젝트 개요
