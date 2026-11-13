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
  namespace: devtools
spec:
  project: platform
  source:
    repoURL: http://gitea-http.devtools.svc.cluster.local:3000/api/packages/gitea-admin/helm  # platform AppProject sourceRepos 허용 목록(in-cluster Gitea)만 가능
    chart: my-app
    targetRevision: "1.0.0"
    helm:
      valueFiles:
        - values.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: devtools  # platform AppProject destinations에 있는 네임스페이스만 가능 (gitops/resources/argocd-projects.yaml)
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

### 릴리스 태깅 & 롤백 (issue #52, D4-A)

`scripts/gitops/push-to-gitea.sh`는 일반 sync 외에 **불변(immutable) 릴리스 태그**를
생성하고 그 태그로 **롤백**하는 두 가지 모드를 제공합니다. 롤백은 `main`을 되돌리는
`reset`/force-push가 **아니라**, 태그 시점의 tree를 재현하는 새 forward commit입니다
— `main`은 selfHeal 대상이자 branch-protected(화이트리스트 `gitea-admin`만 push
가능)이므로 히스토리를 되돌려 쓰는 방식 자체가 불가능하고, 감사 가능성(auditability)
측면에서도 forward-commit 방식이 맞습니다.

```bash
# 1. 현재 gitops/ 트리를 발행한 뒤 그 커밋에 불변 태그를 생성
scripts/gitops/push-to-gitea.sh --tag v1.2.3
# 이미 존재하는 태그를 다시 --tag하면 거부됩니다 (태그는 절대 이동시키지 않음).

# 2. 특정 릴리스로 롤백 — 태그의 tree를 main 위에 새 forward commit으로 발행
scripts/gitops/push-to-gitea.sh --rollback v1.2.3
# 이 스크립트가 실제 git 체크아웃(REPO_ROOT)에서 실행 중이고 GITOPS_DIR이 그 체크아웃
# 안에 있을 때만: gitops/ 워킹 트리에 커밋되지 않은 변경이 있으면 즉시 거부됩니다
# (kubectl/네트워크 호출 이전에, 롤백 도중 로컬 편집이 섞여 들어가는 것을 막기 위함).
# 노드처럼 scripts/만 rsync된 checkout-less 환경(Vagrantfile의 scripts/ 동기화, 예:
# disaster-recovery.md 5번)에서는 이 로컬 검사를 건너뛴다는 로그 한 줄만 남기고 계속
# 진행합니다 — 롤백은 원격 태그만 있으면 되고, 로컬 트리 상태는 필요 없기 때문입니다.
```

두 모드 모두 실행 끝에 한 줄짜리 JSON evidence를 stdout에 출력합니다
(`{"mode":...,"tag":...,"tag_sha":...,"main_sha":...,"timestamp":...}`,
`~/.narwhal/`가 존재하면 `~/.narwhal/gitops-release.json`에도 기록). 이 JSON의
`main_sha`를 ArgoCD가 실제로 수렴했는지 확인하는 데 사용하세요:

```bash
# ArgoCD가 방금 발행/롤백한 커밋을 실제로 반영했는지 확인
kubectl -n devtools get application idp-apps -o jsonpath='{.status.sync.revision}'
# 위 값이 push-to-gitea.sh가 출력한 main_sha와 일치해야 함
```

`idp-apps`는 `HEAD`를 추적하는 app-of-apps 루트(`scripts/cluster/14-gitops-bootstrap.sh`)이고,
`push-to-gitea.sh`는 `ARGOCD_APP`(기본 `narwhal-portal`)뿐 아니라 `idp-apps`에도 hard-refresh
annotation을 요청하므로 보통 즉시 수렴합니다. 그 refresh가 아직 처리되지 않았거나 스크립트를
거치지 않고 확인하는 경우에는 ArgoCD의 폴링 주기(기본 3분) 이내에 수렴하며, 즉시 강제하려면
`argocd app get idp-apps --refresh`를 사용하세요. `idp-apps` 아래의 하위 App들은 각자 `HEAD`를
독립적으로 resolve하므로, 특정 하위 앱의 반영 여부가 궁금하면 그 앱의
`.status.sync.revision`도 별도로 확인해야 합니다.

**로컬 클러스터 없이 스크립트를 검증**하려면 `GITEA_REMOTE_URL` 환경변수로 임시
bare 저장소를 가리켜서 kubectl/port-forward 없이 clone/push 경로만 exercise할 수
있습니다(운영 기본 동작은 이 변수가 비어 있을 때와 동일하게 유지됨):

```bash
git init --bare /tmp/fake-gitea.git
GITEA_REMOTE_URL=/tmp/fake-gitea.git GITOPS_DIR=./gitops \
  scripts/gitops/push-to-gitea.sh "chore: test publish"
```

장애 시나리오(ArgoCD가 롤백 커밋을 못 읽는 등)는
[`disaster-recovery.md` §9 ArgoCD GitOps 장애](disaster-recovery.md#9-argocd-gitops-장애)를 참고하세요.

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

# 업그레이드 전: kubeadm-config가 OIDC 플래그 7개를 갖고 있는지 확인한다.
# kubeadm upgrade는 이 ClusterConfiguration에서 kube-apiserver 정적 파드를 다시 만든다.
# (11-4가 scripts/cluster/11-4c-kubeadm-config-oidc.sh로 기록한다. v1beta4 apiServer.extraArgs는 {name, value} 목록이고,
#  --oidc-ca-file이 가리키는 /etc/kubernetes/pki/oidc-ca.crt는 kubeadm 기본 pki 마운트 안에 있어 extraVolumes가 필요 없다.)
kubectl -n kube-system get cm kubeadm-config -o jsonpath='{.data.ClusterConfiguration}' | yq '.apiServer.extraArgs[] | select(.name | test("^oidc-"))'
# 7개가 없으면: sudo env KUBECONFIG=$HOME/.kube/config-local bash scripts/cluster/11-4c-kubeadm-config-oidc.sh https://keycloak.<DOMAIN>/realms/narwhal

# 업그레이드 적용
sudo kubeadm upgrade apply v1.36.0

# 각 마스터에서 업그레이드 직후: 재생성된 매니페스트에 플래그가 남았는지 확인한다.
# 7이 아니면 11-4b-apiserver-oidc-node.sh(또는 Kakao: ./scripts/cloud/provision-kakao.sh oidc)를 다시 실행한다.
sudo grep -c -- '--oidc-' /etc/kubernetes/manifests/kube-apiserver.yaml
bash scripts/test/verify-cluster.sh   # 'kubeadm-config OIDC'와 'API server OIDC' 항목이 모두 PASS여야 한다

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
- Worker Memory: 8GB (Vagrantfile `WORKER_MEMORY`에서 조정 가능, 2026-11-08 전에는 6GB. 기존 클러스터 적용은 아래 "Worker 메모리 8GB 적용")

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

**CA 교체 시 Harbor 재시작 (#551, D-HARBOR-CA-ROTATE)**: harbor-core 는 `caBundleSecretName`(`narwhal-ca-cert`)을 기동 시에만 읽습니다. CA 가 교체되면 재시작 전까지 Harbor OIDC 로그인이 실패(fail closed)합니다. `scripts/cluster/08-6-tls-routes.sh` 는 `devtools/narwhal-ca-cert` 의 `ca.crt` 가 실제로 바뀌었고 `harbor-core` 가 있을 때만 `rollout restart` 를 수행합니다(재실행해도 변경이 없으면 재시작하지 않음). 08-6 을 거치지 않고 Secret 을 직접 바꿨다면 수동으로 실행합니다.

```bash
kubectl -n devtools rollout restart deployment/harbor-core
```

---

## 9. 백업 확인 및 복구 (Velero)

Velero는 Kubernetes 리소스 및 Persistent Volume 백업을 담당합니다.

### 백업 목록 확인

```bash
# 백업 목록
vagrant ssh master-1 -c "kubectl get backups.velero.io -n storage"

# 백업 상세 정보
vagrant ssh master-1 -c "kubectl describe backups.velero.io <backup-name> -n storage"
```

### 스케줄 백업 확인

```bash
# 백업 스케줄 목록
vagrant ssh master-1 -c "kubectl get schedules.velero.io -n storage"

# 스케줄 상세 정보
vagrant ssh master-1 -c "kubectl describe schedules.velero.io velero-daily-full -n storage"
```

### 수동 백업 생성

```bash
# 전체 클러스터 백업
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup create manual-full-backup"

# 특정 namespace만 백업
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup create manual-gitea-backup --include-namespaces=devtools"

# 여러 namespace 백업
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup create manual-apps-backup \
  --include-namespaces=devtools,iam"

# Label selector로 백업
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup create manual-labeled-backup \
  --selector='app=my-app'"
```

### 백업 상태 확인

```bash
# 백업 진행 상황
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup describe <backup-name>"

# 백업 로그 확인
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup logs <backup-name>"

# 백업 저장소 확인 (SeaweedFS S3 버킷 velero; 노드 로컬 디렉터리 아님)
vagrant ssh master-1 -c "kubectl get backupstoragelocations.velero.io -n storage"
```

### 복구 (Restore)

```bash
# 백업에서 복구
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero restore create --from-backup <backup-name>"

# 특정 namespace만 복구
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero restore create restore-gitea \
  --from-backup <backup-name> \
  --include-namespaces=devtools"

# 복구 상태 확인
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero restore describe <restore-name>"

# 복구 로그 확인
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero restore logs <restore-name>"
```

### 백업 삭제

```bash
# 백업 삭제 (스토리지에서도 삭제)
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup delete <backup-name>"

# 오래된 백업 자동 삭제 (30일 이상)
vagrant ssh master-1 -c "kubectl exec -n storage deployment/velero -- \
  velero backup delete --older-than 720h"
```

**주의사항**:

- Velero는 `upgradeCRDs: false` 설정 필수 (musl/glibc 비호환)
- 백업 저장소는 SeaweedFS S3(버킷 `velero`, `http://seaweedfs-s3.storage.svc.cluster.local:8333`)이며 NFS 디렉터리가 아님
- PVC 백업은 Kopia(`uploaderType: kopia`) 파일시스템 백업이며, NFS `root_squash`에서 읽을 수 없는 owner-only 볼륨 5종(OpenBao `data`, CNPG `pgdata`, Gitea `data`, Harbor `job-logs`, SeaweedFS master `data-storage`)은 제외되어 Velero가 보호하지 않음 (`docs/common/storage-protection-policy.md`)
- 복구 시 기존 리소스와 충돌할 수 있습니다. **보호되지 않는 데이터가 있는 네임스페이스나 PVC는 삭제하지 마세요.**
  Velero가 보호하지 않는 볼륨(Gitea `data`, OpenBao `data`(활성화되고 클러스터 밖으로 복사한 암호화 스냅샷이
  없는 경우), Harbor jobservice `job-logs`, SeaweedFS master `data-storage`, CNPG `pgdata`(Barman 백업만 있음))를
  가진 네임스페이스(`devtools`, `storage`, `database`)를 지우면 그 데이터는 복구할 수 없습니다. 대신 문제 파드를
  재시작하고(PVC는 유지), 원인이 RBAC·설정이면 그 리소스를 GitOps(`push-to-gitea.sh`)로 제자리에서 고치세요.
  네임스페이스 삭제 후 복구가 필요하면 먼저 `docs/vagrant/disaster-recovery.md`의 데이터별 복구 경로와 외부 사본
  여부를 확인하세요.

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

## 11. 베이스 box 보관과 복원 (D-BOX-CATALOG)

`dasomel/ubuntu-26.04-xfs`는 GitHub Pages 카탈로그(Vagrantfile의 `box_url`)가 가리키는 GitHub Release 자산에서 받아 온다. Vagrant Cloud는 2026-12-31에 호스팅 box를 삭제하고, 카탈로그는 `dasomel.github.io` 한 경로(`cne.io.kr`로 리디렉션)에 의존하므로, 클러스터를 다시 만들 수 있도록 캐시된 box의 로컬 사본을 한 벌 두는 것을 권장한다. 사본은 수 GB이니 저장소 밖에 둔다.

### 사본 만들기

```bash
vagrant box list | grep ubuntu-26.04-xfs            # 캐시 확인: (vmware_desktop, 0.3.1, (arm64))
mkdir -p ~/box-backup && cd ~/box-backup            # 저장소 밖에서 실행 (cwd에 Vagrantfile이 있으면 package.name이 출력명을 바꿀 수 있음)
vagrant box repackage dasomel/ubuntu-26.04-xfs vmware_desktop 0.3.1   # ./package.box 생성
shasum -a 256 package.box | tee package.box.sha256
```

`repackage`는 압축 해제된 캐시 디렉터리를 다시 `tar.gz`로 묶는다(Vagrant 2.4.9 소스: `bsdtar -czf`). 그래서 이 해시는 **카탈로그의 sha256(원본 자산 `ubuntu-26.04-xfs-vmware`의 해시)과 일치하지 않는 것이 정상**이다. 사본의 무결성은 위에서 기록한 자체 해시로 확인하고, 카탈로그 값은 원본 자산을 직접 받아 비교할 때만 쓴다.

```bash
curl -fsSL https://dasomel.github.io/kube-ready-box/boxes/ubuntu-26.04-xfs.json | jq -r '.versions[] | select(.version=="0.3.1") | .providers[] | "\(.name) \(.architecture) \(.checksum)"'
curl -fL -o ubuntu-26.04-xfs-vmware-0.3.1.box https://github.com/dasomel/kube-ready-box/releases/download/v0.3.1/ubuntu-26.04-xfs-vmware
shasum -a 256 ubuntu-26.04-xfs-vmware-0.3.1.box     # 카탈로그 vmware_desktop(arm64) 값과 같아야 한다
```

원본 자산을 받아 두면 이것이 카탈로그 해시로 검증되는 가장 믿을 만한 사본이다.

### 사본에서 복원

`.box` 파일을 바로 `vagrant box add --name dasomel/ubuntu-26.04-xfs package.box` 하면 버전이 `0`으로 등록되어 Vagrantfile의 `box_version = "0.3.1"`을 만족하지 못하고, `--box-version`은 로컬 파일에서 거부된다(Vagrant 2.4.9에서 확인). 버전을 지키려면 로컬 메타데이터 JSON을 거친다.

```bash
cat > box-meta.json <<EOF
{"name":"dasomel/ubuntu-26.04-xfs","versions":[{"version":"0.3.1","providers":[{"name":"vmware_desktop","url":"file://$PWD/package.box","checksum_type":"sha256","checksum":"$(cut -d' ' -f1 package.box.sha256)","architecture":"arm64","default_architecture":true}]}]}
EOF
vagrant box add --name dasomel/ubuntu-26.04-xfs box-meta.json   # 'Successfully added box ... (v0.3.1)' 확인
vagrant box list | grep ubuntu-26.04-xfs
```

원본 자산을 복원할 때는 `url`을 그 파일로, `checksum`을 카탈로그 값으로 바꾼다. Vagrantfile의 `box_url`은 그대로 두어도 된다. 이 절차 중 메타데이터 JSON 복원과 버전 `0` 동작은 임시 `VAGRANT_HOME`의 작은 가짜 box로 확인했고, 실제 2GB급 box의 `repackage`는 실행하지 않았다.

---

## 12. VM 디스크 증가와 회수 (D-VM-DISK-GUARD)

### 왜 줄지 않는가

각 VM 디스크는 성장형 sparse linked clone이다. 게스트가 한 번이라도 쓴 블록은 호스트에 계속 할당된 채 남는다. NVMe 가상 디스크는 discard(`DISC-GRAN 0`)를 받지 않아 `fstrim`이 호스트 블록을 풀지 못하고, linked clone은 `vmware-toolbox-cmd disk shrink`가 비활성이라 줄일 수 없다. 빈 공간을 0으로 채우는 방법도 0 블록이 그대로 할당되어 오히려 커진다(6GiB를 쓰자 호스트 디렉터리가 2.6GiB 늘었음). 그래서 게스트 `df`가 43G인데 호스트 vmdk가 298G가 될 수 있다(worker-3, 2026-11-05).

커지는 속도는 쓰기 총량이 아니라 **새로 할당되는 블록**이 정한다. 2026-11-06 측정에서 control-plane은 하루 27~54GB(etcd WAL + master-1의 NFS 쓰기)를 쓰지만 같은 블록을 덮어써 vmdk가 9~25G에 머물렀고, worker-2는 평소 하루 약 11GB인데 스캔/재스케줄 폭주 한 시간에 44GB를 써서 35G에서 82G로 커졌다.

### 조기 발견

- 호스트: `scripts/ops/check-vm-disk.sh`가 VM별 호스트 vmdk 디렉터리 크기와 게스트 `df` 사용량을 비교한다. 차이가 30GB 이상이거나 vmdk가 120GB 이상이면, 또는 호스트 여유 공간이 40GB 미만이면 `[WARN]`을 낸다(`VM_DISK_WARN_GAP_GB`, `VM_DISK_WARN_ABS_GB`, `VM_DISK_MIN_FREE_GB`로 조정, `--strict`면 종료 코드 1). `scripts/up.sh`가 시작할 때 경고 전용으로 실행한다.
- 클러스터: 워커의 6시간 평균 디스크 쓰기가 하루 20GB를 넘으면 `NodeDiskWriteRateHigh`가 울린다. 쓰기 주체는 `topk(15, sum by (namespace,pod,container) (rate(container_fs_writes_bytes_total[1h])))`로 찾는다.

### 회수 절차: 워커를 재생성한다

vmdk를 줄이는 방법은 없고, 노드를 지우고 다시 만드는 것이 유일한 회수 경로다. 한 번에 한 노드씩, 이 순서를 지킨다.

1. **노드 로컬 데이터 확인.** 재생성하면 노드 디스크의 모든 것이 사라진다. NFS PV는 master-1에 있어 영향이 없지만 아래는 노드에만 있다.

   ```bash
   kubectl get nodes -l narwhal.io/seaweedfs-volume=true      # SeaweedFS 볼륨 서버(hostPath /ssd/object_store, Velero 백업 저장소)
   kubectl get pods -A -o wide --field-selector spec.nodeName=<node>   # emptyDir와 hostPath를 쓰는 pod 확인
   ```

   `narwhal.io/seaweedfs-volume=true` 라벨이 붙은 노드는 `/ssd/object_store`를 먼저 다른 곳에 복사하고 재생성 뒤에 되돌려야 한다. 이 라벨은 08-4가 한 번만 붙이고 옮기지 않는다(D-sw-pin). 복사 없이 재생성하면 Velero 저장소가 사라진다.
2. **drain.** `kubectl cordon <node>` 후 `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`. CNPG 인스턴스와 etcd 정족수(master)에 영향이 없는지 먼저 본다. 마스터는 이 절차 대상이 아니다.
3. **삭제와 재생성.** `vagrant destroy -f <vm>` 다음 `./scripts/up.sh`(맨 `vagrant up`은 Phase 2 경로가 아니다). 새 노드는 새 linked clone이므로 크기가 처음으로 돌아간다.
4. **확인.** `kubectl get nodes`가 모두 Ready이고 cordon이 없으며, `scripts/ops/check-vm-disk.sh`에 경고가 없고, 1단계에서 복사한 데이터를 되돌렸는지 확인한다. 노드가 새로 만들어진 직후 한동안은 이미지 pull과 스캔 폭주로 쓰기가 크므로 `NodeDiskWriteRateHigh`가 잠시 울릴 수 있다.

---

## 13. Worker 메모리 8GB 적용 (D-WORKER-MEMORY)

2026-11-08 결정으로 `WORKER_MEMORY`가 6144에서 8192로 올랐다(마스터는 6GB 유지). 새로 만드는 클러스터는 그대로 8GB다. 이미 떠 있는 클러스터는 VM의 `memsize`가 **VM이 시작될 때** 적용되므로, Vagrantfile만 바꾸고는 반영되지 않고 워커를 한 대씩 껐다 켜야 한다. 호스트 RAM은 VM 합계 42GB(마스터 3x6 + 워커 3x8)가 필요하다.

**한 번에 한 대만 한다.** 다음 워커는 앞 워커가 Ready이고 CNPG가 정상일 때 시작한다.

1. **사전 확인.** `kubectl get nodes`가 모두 Ready이고 `kubectl get cluster -A`의 CNPG 인스턴스가 모두 정상인지, 이 워커가 `narwhal.io/seaweedfs-volume=true` 노드이면 그 데이터는 `halt`로는 사라지지 않지만 드레인 동안 Velero 백업이 실패할 수 있으므로 백업 시각을 피한다.
2. **cordon과 drain.** `kubectl cordon <worker>` 후 `kubectl drain <worker> --ignore-daemonsets --delete-emptydir-data`.
3. **재시작.** `vagrant halt <worker> && vagrant up <worker> --no-provision`. `--no-provision`이 없으면 프로비저닝 스크립트가 다시 돈다.
4. **uncordon.** `kubectl uncordon <worker>`.
5. **확인.** `kubectl get node <worker> -o jsonpath='{.status.allocatable.memory}'`가 약 7.3GiB(이전 약 5.3GiB)이고, 노드가 Ready이며, 이 워커에 있던 Pod이 Running으로 돌아왔는지 본다. 그 뒤에 다음 워커로 간다.

**CNPG required anti-affinity 주의(#590).** `narwhal-db`는 인스턴스 2개에 `podAntiAffinityType=required`(R313)라 두 인스턴스가 항상 서로 다른 워커에 있다. 비우려는 워커에 인스턴스가 있으면, 쫓겨난 인스턴스는 인스턴스가 없는 남은 한 워커로만 갈 수 있고, 그 워커에 requests 여유가 없으면 **Pending으로 남는다**. 그래서 (1) drain **전에** `kubectl -n database get cluster narwhal-db -o jsonpath='{.status.currentPrimary}'`와 Pod의 노드를 보고, 그 워커에 primary가 있으면 먼저 `kubectl cnpg promote narwhal-db <다른 인스턴스> -n database`로 switchover한다(건강한 primary가 drain으로 쫓겨나 재배치를 기다리는 일을 피한다). (2) 워커를 동시에 두 대 이상 내리지 않는다. (3) uncordon 뒤 `kubectl -n database get cluster narwhal-db`가 `Cluster in healthy state`이고 두 인스턴스가 Running인지 확인하고 다음 워커로 간다.

롤백은 Vagrantfile을 6144로 되돌리고 같은 절차를 반복한다. 호스트 RAM이 모자라면 이 절차를 하지 말고 6144를 유지한다.

---

## 14. 호스트 슬립과 메시 인증서 (D-MESH-CERT-TTL)

호스트(Mac)가 슬립하면 VM이 멈추고 VM의 monotonic 시계도 멈춘다. wall clock은 깨어난 뒤 NTP로 보정되지만, Istio ambient의 인증서 갱신 타이머는 monotonic 기준이라 정지가 길면 인증서가 이미 만료된 뒤에 갱신을 시도한다(2026-11-07: ztunnel 6개와 waypoint 3개의 인증서 만료, CNPG Ready=False, WAL 아카이빙이 약 20시간 실패, #600).

**예방 1: 클러스터가 도는 동안 호스트를 깨워 둔다.** 터미널에서 `caffeinate -dimsu &`(디스플레이, 유휴, 디스크, 시스템 슬립 방지, AC 전원 가정)를 띄우거나, 상시 운영 호스트면 `sudo pmset -a sleep 0`으로 시스템 슬립을 끈다. `scripts/up.sh`는 호스트 슬립이 켜져 있고 `caffeinate`가 없으면 경고 한 줄을 출력한다(경고만 하고 중단하지 않는다). 노트북 덮개를 닫으면 `caffeinate`로도 슬립한다(클램셸 조건은 별도).

**예방 2: 인증서 TTL을 168h로 늘렸다.** ztunnel `SECRET_TTL`, waypoint `meshConfig.defaultConfig.proxyMetadata.SECRET_TTL`, istiod `DEFAULT_WORKLOAD_CERT_TTL`/`MAX_WORKLOAD_CERT_TTL`(168h/720h). 갱신은 TTL의 약 절반에서 일어나므로 인증서에는 항상 약 84시간 이상 남아 있어, 약 3.5일보다 짧은 슬립은 스스로 복구된다. 대가는 탈취된 workload 키의 유효 기간이 1일에서 7일로 늘어난 것이다. 근거와 소스 위치는 `gitops/charts/narwhal-apps/templates/istiod.yaml`의 D-MESH-CERT-TTL, 고정은 R324. 적용은 ArgoCD sync(또는 `09-istio-ambient.sh` 재실행) **뒤에** 아래 복구 절차로 파드를 재시작해야 새 TTL로 발급된다.

**탐지.** `IstioWaypointCertExpiringSoon`(waypoint 인증서 잔여 24시간 미만 또는 만료)과 `CNPGInstanceNotReady`. ztunnel 만료는 직접 메트릭이 없고 Loki 알림 규칙도 구성되어 있지 않아, 위 두 경보로 간접 탐지한다. WAL 아카이빙 실패 경보는 CNPG 메트릭 수집이 꺼져 있어(`enablePodMonitor: false`) 아직 없다.

**복구(슬립 뒤 증상이 보일 때).** 노드가 모두 Ready인지 먼저 본 뒤 ztunnel을 롤링하고, 이어서 waypoint를 재시작한다.

```bash
kubectl --context narwhal -n istio-system rollout restart daemonset/ztunnel
kubectl --context narwhal -n istio-system rollout status daemonset/ztunnel --timeout=300s
for ns in $(kubectl --context narwhal get deploy -A -l gateway.networking.k8s.io/gateway-name=waypoint -o jsonpath='{range .items[*]}{.metadata.namespace}{"\n"}{end}' | sort -u); do
  kubectl --context narwhal -n "$ns" rollout restart deployment/waypoint
done
kubectl --context narwhal -n database get cluster narwhal-db   # Cluster in healthy state
```

만료가 오래 방치됐다면 CNPG 인스턴스(`narwhal-db-N`)와 velero-server도 메시가 돌아온 뒤 재시작이 필요할 수 있다. 재시작 뒤 waypoint가 새 TTL로 렌더링됐는지 `kubectl --context narwhal -n monitoring get deploy waypoint -o yaml | grep -A1 SECRET_TTL`로 확인한다(없으면 Deployment가 아직 옛 템플릿이므로 istiod를 재시작해 Gateway를 다시 조정시킨다). 확인: `istio_agent_cert_expiry_seconds`가 약 604800(7일)에 가까운지, ztunnel 로그에 `certificate expired`가 더 나오지 않는지 본다.

---

## 15. NFS export 전용 디스크 (D-NFS-VMDK)

2026-11-08 소유자 결정: NFS export(`/srv/nfs/k8s`)를 master-1의 루트 디스크가 아닌 **별도 가상 디스크**에 둔다. 사고(2026-11-08)에서 nfs-csi PV 17개가 etcd와 같은 VMDK를 공유했고, NFS 쓰기 폭주로 etcd WAL fsync가 8초를 넘겨 apiserver 타임아웃과 CNPG 재시작/failover가 났다. 디스크를 둘로 나눠도 두 VMDK 파일은 같은 호스트 SSD에 있으므로, 게스트 안의 I/O 큐와 파일시스템 fsync 순서 결합이 끊어질 뿐 호스트 I/O가 격리되는 것은 아니다. 이 효과는 아직 측정하지 않았다.

### 새 클러스터

`Vagrantfile`의 `NFS_DISK_GB`(기본 200, `NARWHAL_NFS_DISK_GB`로 변경, 디스크를 붙이기 전에만 `0`으로 예전 배치를 쓸 수 있다)가 디스크 크기를 정하고 `NFS_DATA_DEVICE=auto`를 `01-nfs-server.sh`에 넘긴다. `auto`는 파티션도 마운트도 없고 파일시스템이 없는(또는 라벨 `narwhal-nfs`인 XFS) 디스크가 **정확히 1개**일 때만 그 디스크를 쓰고, 0개나 2개 이상이면 추측하지 않고 실패한다.

**vmware_desktop에서는 `config.vm.disk`를 쓰지 않는다(D-NFS-VMDK-VMX).** vagrant-vmware-desktop 3.0.5의 디스크 처리(`cap/disk.rb` `get_disk`)가 이 박스의 vmx에서 `File.extname(nil)`로 죽어 master-1 부팅이 전부 깨졌다(2026-11-08). 대신 master-1의 `before [:up, :reload]` 트리거가 호스트에서 `vmware-vdiskmanager -c -s <N>GB -a lsilogic -t 0 narwhal-nfs.vmdk`로 성장형(sparse) vmdk를 만들고(이미 있으면 건드리지 않는다), provider vmx 키 `nvme0:1.present = "TRUE"`와 `nvme0:1.fileName = "narwhal-nfs.vmdk"`가 NVMe 버스(박스의 OS 디스크 `nvme0:0`과 같은 버스)에 붙인다. 기존 머신은 VM 디렉터리(`.vagrant/machines/master-1/vmware_desktop/<uuid>/`) 안의 파일을 쓰고, 첫 `up`처럼 VM 디렉터리가 아직 없으면 `.vagrant/narwhal-nfs/narwhal-nfs.vmdk` 절대 경로를 쓴다(`destroy` 뒤 트리거가 지운다). 플러그인의 디스크 정리는 `disk_meta`가 비어 있어 아무것도 지우지 않으므로 이 디스크를 삭제할 수 없다. 200은 상한이지 예약이 아니며 크기는 만들 때만 적용된다(이후 변경은 무시). VirtualBox는 `config.vm.disk`(vdi)를 그대로 쓰며 이 변경에서 실행해 보지 않았다.

**게스트에서는 새 디스크가 `/dev/nvme0n1`로 보이고 OS 디스크가 `/dev/nvme0n2`로 밀린다.** 루트가 UUID 기반 LVM이라 문제없다. 그래도 장치 이름을 하드코딩하지 말고 `lsblk`로 확인한다.

**이미 디스크가 붙은 머신에서 `NARWHAL_NFS_DISK_GB=0`을 쓰면 `Vagrantfile`이 거부하고 중단한다(R327).** 디스크 없이 부팅하면 루트 디스크에 남은 PV 옛 복사본을 export하게 된다. 판단 기준은 VM 디렉터리(또는 절대 경로)의 `narwhal-nfs.vmdk`, 머신 디렉터리의 `nfs-data*`, `disk_meta`의 `nfs-data`다. 데이터가 이미 없어진 클러스터를 `destroy`할 때만 `NARWHAL_NFS_DISK_ALLOW_DELETE=yes-delete-the-nfs-disk`로 우회한다. 디스크 이름 `narwhal-nfs.vmdk`는 바꾸지 않는다(라이브 vmx가 이 이름을 가리킨다). 포맷은 XFS(`prjquota`), fstab은 UUID로 쓰므로 장치 이름에 의존하지 않는다.

### 기존 클러스터 이전 (소유자 실행 점검 작업)

**소유자 승인과 점검 시간이 필요하다.** master-1은 etcd 멤버이자 모든 PV의 NFS 서버라서, 디스크를 붙이려면 VM을 끄는 동안 NFS를 쓰는 모든 워크로드가 멈춘다(VMware는 실행 중인 VM에 디스크를 붙일 수 없다). 예상 중단 시간은 복사할 데이터 양에 달렸고 측정하지 않았다. 데이터가 100GB 미만이라면 쓰기 중단부터 복구까지 30~60분 정도로 추정한다(`sudo du -sx /srv/nfs/k8s`로 먼저 크기를 확인하고, 크면 늘려 잡는다). 쓰기를 모두 멈춘 뒤 한 번만 복사하므로 사전 online 복사 단계는 없다.

**원칙: 이 절차는 `docs/runbooks/nfs-xfs-migration.md`의 3~7단계(쓰기 중단 순서, hostPath 사전 확인, 복구 순서, 검증, 롤백)와 같다.** 같은 순서를 두 곳에 복제하지 않으므로, 아래는 이 변경에 고유한 부분(디스크 붙이기)만 적고 나머지는 그 문서를 따른다.

1. **사전 확인.** 섹션 16(호스트 부하)을 지킨다. `kubectl get nodes`가 모두 Ready이고, master-2/3의 etcd가 건강한지(`kubectl -n kube-system get pods -l component=etcd`) 본다. master-1이 꺼져도 etcd는 3개 중 2개로 정족수를 유지하고 VIP(kube-vip)가 apiserver를 다른 마스터로 넘긴다. 이 중 하나라도 불안하면 하지 않는다. `scripts/ops/check-vm-disk.sh`로 호스트 여유 공간을 본다.
2. **백업(쓰기 중단 전, 모두 클러스터 밖에 보관).**
   - Velero: 섹션 9의 수동 백업을 만들고 `Completed`를 확인한다. CNPG pgdata는 Velero fs-backup에서 제외돼 있으므로(D-pvb-exclude) 별도로 논리 덤프를 받는다: `kubectl -n database exec <primary> -c postgres -- pg_dumpall -U postgres | gzip > narwhal-db-$(date +%F).sql.gz`(호스트에서 실행하고 비밀번호는 출력하지 않는다).
   - etcd 스냅샷(master-1의 etcd Pod에서):

     ```bash
     kubectl -n kube-system exec etcd-narwhal-master-1 -- etcdctl --endpoints=https://127.0.0.1:2379 \
       --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt \
       --key=/etc/kubernetes/pki/etcd/server.key snapshot save /var/lib/etcd/snapshot-pre-nfs-disk.db
     ```

     그 파일을 호스트로 복사해 둔다(`vagrant ssh master-1 -c 'sudo cat /var/lib/etcd/snapshot-pre-nfs-disk.db' > ...`).
3. **쓰기 중단.** `nfs-xfs-migration.md` 3단계를 그대로 한다(hostPath 사전 확인, ArgoCD autosync 중지, 정해진 순서로 scale down, CNPG hibernate, 클라이언트 0 확인). 마지막에 master-1에서 `sudo systemctl stop nfs-server`. **`nfs-server`는 부팅 때 자동으로 시작되므로**(4단계 재부팅 뒤 옛 루트 디스크 복사본을 다시 export한다) 4단계에서 올라온 직후 다시 `sudo systemctl stop nfs-server`를 한다.
4. **디스크 붙이기.** 호스트에서 최신 `Vagrantfile`을 받은 뒤 VM을 끄고, 가장 간단한 방법은 `vagrant up master-1 --no-provision`(트리거가 vmdk를 만들고 vmx 키가 붙인다)이다. 수동으로 똑같이 하려면 VM이 꺼진 상태에서 VM 디렉터리(`dirname $(cat .vagrant/machines/master-1/vmware_desktop/id)`)에서:

   ```bash
   vagrant halt master-1
   "/Applications/VMware Fusion.app/Contents/Library/vmware-vdiskmanager" -c -s 200GB -a lsilogic -t 0 narwhal-nfs.vmdk
   printf '%s\n' 'nvme0:1.present = "TRUE"' 'nvme0:1.fileName = "narwhal-nfs.vmdk"' >> ubuntu-26.04-vmware-arm64.vmx
   vagrant up master-1 --no-provision
   vagrant ssh master-1 -c 'lsblk -dpo NAME,SIZE,TYPE,FSTYPE,LABEL'   # 새 빈 디스크(FSTYPE 비어 있음)를 확인
   vagrant ssh master-1 -c 'sudo systemctl stop nfs-server'            # 부팅 때 자동 시작됨: 옛 복사본을 export하지 않게 즉시 멈춘다
   ```

   새 디스크는 `/dev/nvme0n1`, OS 디스크는 `/dev/nvme0n2`로 보인다(루트는 UUID 기반 LVM이라 정상). `--no-provision`을 빼면 프로비저닝 스크립트가 다시 돈다. 노드가 Ready로 돌아오고 etcd 멤버가 복귀할 때까지 기다린다(`kubectl get nodes`, etcd 로그).
5. **포맷과 복사(master-1).** `DEV`는 4단계에서 확인한 빈 디스크다. 이미 파일시스템이 있으면 중단한다.

   ```bash
   DEV=/dev/nvme0n1  # 4단계 lsblk 결과로 정한다(새 디스크는 FSTYPE이 비어 있다). 추측하지 않는다.
   test -z "$(sudo blkid -p -o value -s TYPE "$DEV")" || { echo "filesystem exists, stop"; false; }
   sudo mkfs.xfs -L narwhal-nfs "$DEV"
   sudo mkdir -p /mnt/nfs-new && sudo mount -o prjquota "$DEV" /mnt/nfs-new
   sudo rsync -aHAX --numeric-ids --sparse --info=progress2 /srv/nfs/k8s/ /mnt/nfs-new/
   sudo rsync -aHAXn --numeric-ids --sparse --delete --itemize-changes /srv/nfs/k8s/ /mnt/nfs-new/   # 아무것도 출력하지 않아야 함
   sudo umount /mnt/nfs-new
   ```

6. **전환.** `nfs-xfs-migration.md` 4단계의 마운트 블록(UUID fstab 줄, `RequiresMountsFor`, `mount /srv/nfs/k8s`)을 그대로 실행하고 `findmnt -no FSTYPE,OPTIONS /srv/nfs/k8s`가 `xfs`와 `prjquota`를 보이는지 확인한다. 예전 루트 디스크의 데이터는 마운트 아래에 롤백용으로 남는다.
7. **프로젝트 쿼터 복원.** `nfs-quota-agent`는 프로젝트 쿼터(`prjquota`)로 PV 용량을 강제한다. rsync는 XFS 프로젝트 ID와 쿼터 한도를 복사하지 않는다(ID는 inode, 한도는 파일시스템의 쿼터 메타데이터에 있다). `/etc/projects`와 `/etc/projid`는 루트 디스크에 남아 있으므로 ID는 다시 입히고, 한도는 agent가 다시 적용하는지 확인한다. **주의: 호스트의 `/etc/projects`에는 agent 컨테이너 기준 경로(`/export/...`)가 들어 있다.** agent는 `/srv/nfs/k8s`를 컨테이너의 `/export`로 마운트하고(`05-nfs-quota-agent.sh`의 `nfsExport.hostPath`와 `nfsBasePath=/export`) `/etc/projects`와 `/etc/projid`를 hostPath로 공유한다. 그래서 호스트에서 인자 없는 `xfs_quota -c 'project -s'`를 쓰면 없는 경로를 찾아 실패한다. 경로를 명시적으로 바꿔서 한 항목씩 적용한다.

   ```bash
   # export 루트는 nobody 소유 0750이라 일반 사용자는 들어가지 못한다: 존재 확인은 반드시 sudo test -d
   head -3 /etc/projects     # 먼저 형식(<id>:<경로>)과 /export 접두사를 눈으로 확인한다
   sed 's|^\([0-9]*\):/export|\1:/srv/nfs/k8s|' /etc/projects | while IFS=: read -r id path; do
     [ -n "$id" ] && sudo test -d "$path" && sudo xfs_quota -x -c "project -s -p ${path} ${id}" /srv/nfs/k8s
   done
   sudo systemctl start nfs-server && sudo exportfs -v
   ```

   agent를 되살린 뒤 `sudo xfs_quota -x -c 'report -p -h' /srv/nfs/k8s`에서 PV마다 한도가 보여야 한다. 한도가 비어 있으면 agent Pod를 재시작하고 로그가 applied인지 본다. 위 명령은 05 스크립트와 라이브 DaemonSet의 마운트(`/export`, `/etc/projects`, `/etc/projid`)를 읽고 쓴 것이며 아직 실행해 검증하지 않았고, 한도 재적용 동작도 마찬가지다. `test -d`를 sudo 없이 쓰면 0750 루트 때문에 모든 항목이 건너뛰어지므로 반드시 `sudo test -d`다. **quota agent DaemonSet을 멈추려고 `nodeSelector` 키를 추가했다면 복구할 때 주의한다:** merge patch(`kubectl patch --type merge`)는 추가한 키를 지우지 않고 그대로 둔다. 키 제거는 JSON patch로 한다(`kubectl -n <ns> patch ds <name> --type json -p '[{"op":"remove","path":"/spec/template/spec/nodeSelector/<key>"}]'`, 키 안의 `/`는 `~1`). 복구 뒤 `kubectl get ds`의 DESIRED가 노드 수와 같은지 확인한다. 그래서 8단계 검증에서 쿼터 초과 쓰기 테스트(`nfs-xfs-migration.md` 6단계)를 반드시 한다.
8. **복구와 검증.** `nfs-xfs-migration.md` 5, 6단계의 역순 복구와 검증을 한다. 추가로 모든 PV가 실제로 마운트되는지(`kubectl get pv`가 전부 Bound이고 NFS를 쓰는 Pod가 Running), 그리고 etcd 효과를 본다.

   ```promql
   histogram_quantile(0.99, sum by (le, instance) (rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])))
   ```

   부하 시점의 p99를 이전 값과 비교한다. 개선이 없으면 병목은 호스트 SSD다.
9. **롤백.** `nfs-xfs-migration.md` 7단계. 디스크를 떼는 것은 필요 없고, fstab 줄과 `RequiresMountsFor` drop-in을 되돌려 루트의 예전 복사본을 쓰면 된다(전환 뒤 쓰인 데이터는 먼저 되돌려 복사). 예전 복사본은 며칠 검증한 뒤에 지운다. 루트 vmdk는 줄어들지 않으므로(섹션 12) 지워도 호스트 공간은 돌아오지 않는다.

**이전하지 않으면.** 새 `Vagrantfile`을 받은 상태에서 master-1을 `halt`/`up`하면 트리거가 빈 vmdk를 만들어 붙이기만 하고(포맷과 마운트는 하지 않음) NFS는 예전 루트 디스크에서 계속 나간다. `01-nfs-server.sh`를 `--provision`으로 다시 돌리면 `auto`가 빈 디스크를 찾았더라도 `/srv/nfs/k8s`가 비어 있지 않아 마운트를 거부하고 실패한다(데이터를 덮지 않으려는 설계). 이전을 미루려면 새 `Vagrantfile`을 받지 말고 옛 버전으로 두거나 `NARWHAL_NFS_DISK_GB=0`으로 올린다(디스크가 아직 없을 때만 가능하고, 있으면 거부된다).

---

## 16. 호스트 부하 규칙

VM 6대(마스터 3 x 6GB, 워커 3 x 8GB)가 호스트 한 대의 CPU, 메모리, SSD를 나눠 쓴다. 호스트가 바쁘면 게스트의 etcd fsync와 CNPG liveness가 먼저 늦어진다(섹션 15의 사고).

- **동시에 하지 않는다.** 롤링 재부팅/재시작(워커 메모리 섹션 13, 인증서 복구 섹션 14, 마스터 작업), 전체 테스트(`make test`, `make e2e`, `scripts/test/*` 라이브 검사), 이미지 대량 pull/빌드, 다른 VM이나 컨테이너(다른 클러스터, Docker Desktop, 다른 Vagrant 환경)는 한 번에 하나만 한다.
- **점검 전에 호스트 부하를 본다.** `uptime`의 1분 load average, `sysctl -n hw.ncpu`로 얻는 코어 수(경험칙: load가 코어 수의 절반 미만), `sysctl vm.swapusage`(swap이 늘고 있지 않을 것), `scripts/ops/check-vm-disk.sh`(호스트 여유 공간). 이 기준은 측정으로 정한 값이 아니라 경험칙이다.
- **클러스터 쪽도 본다.** `kubectl top nodes`, `kubectl -n database get cluster narwhal-db`(Cluster in healthy state), 그리고 etcd fsync p99(섹션 15의 쿼리)가 평소 수준인지 확인한 뒤 시작한다.
- **한 단계 끝나면 가라앉는 것을 확인하고 다음으로 간다.** 노드나 Pod 재시작 직후에는 이미지 pull과 재동기화로 부하가 한동안 높다.

---

## 관련 문서

- [architecture.md](../common/architecture.md) - 아키텍처 개요
- [dns-access.md](dns-access.md) - 서비스 URL, SSO 로그인, 기본 자격 증명
- [dns-access.md](dns-access.md) - DNS 및 접근 방법
- [database.md](../common/database.md) - 데이터베이스 관리
- [troubleshooting.md](../common/troubleshooting.md) - 트러블슈팅 가이드
- [../VERSIONS.md](../../VERSIONS.md) - 컴포넌트 버전 관리
- [../README.md](../../README.md) - 프로젝트 개요

## 17. Single-node local topology (D-SINGLE-NODE)

The default remains `NARWHAL_TOPOLOGY=ha`: three 2-vCPU/6144-MB
masters and three 4-vCPU/8192-MB workers. Release and HA verification must use it.

For a fresh disposable local cluster:

```bash
NARWHAL_TOPOLOGY=single ./scripts/up.sh
# Optional overrides: NARWHAL_SINGLE_CPUS=10 NARWHAL_SINGLE_MEMORY_MB=24576
```

Only master-1 is defined, with defaults 10 vCPU/24576 MB (the local host budget
ceiling). Both sizing overrides must be positive integers. Cilium installs one
operator replica; the single kubelet allows 200 pods instead of the default 110.

`NARWHAL_TOPOLOGY` accepts `ha` or `single`; unset/empty uses
`.vagrant/narwhal-topology` while nonempty provider `id` files exist, otherwise
`ha`. The Vagrantfile writes this record before up/reload, so later bare
`vagrant up` or `vagrant reload` retains single. `scripts/up.sh` resolves the
same record and exports the result. A contradictory explicit value aborts,
naming the requested and recorded topology. Created machines absent from the
selected definition also cause an abort; empty leftover machine directories
are harmless.

To switch topology, back up data as needed, destroy **all** machines using the
recorded topology, then run `NARWHAL_TOPOLOGY=ha ./scripts/up.sh` (or `single`).
After the final provider ID disappears the record is ignored by the shell
resolver and automatically removed at the next Vagrantfile evaluation.
Do not delete ID files to bypass the guards. This is a fresh-cluster workflow,
not an in-place conversion of Kubernetes Nodes or etcd membership.

IPs, the API VIP, MetalLB range, domain and master-1 NFS disk remain unchanged.
Do not run HA and single fleets concurrently: they share those addresses.
The existing NFS disk guard still applies; never set
`NARWHAL_NFS_DISK_GB=0` to bypass topology checks (see section 15).
kube-vip retains its post-init static Pod and leader election; one member can
hold the lease, but there is no failover peer. Phase 1 removes the control-plane
taint as before; Phase 2 keeps it removed only in single. The reboot healer also
waits for one Ready node instead of the HA threshold of five.

The single workload profile uses the existing GitOps **staged copy** path:
`scripts/gitops/apply-topology.sh` changes the CNPG bootstrap manifest and the
copied Gitea tree, never the checked-in HA manifests. CNPG runs one instance;
cert-manager controller/webhook/cainjector, all four Kyverno controllers and
istiod run one replica each. Bootstrap and later `push-to-gitea.sh` publications
apply the same transformation, so reconciliation reads the single settings
from Git. This is not a temporary live patch. PDBs are retained: istiod,
cert-manager and chart-generated budgets can block drain of the last Ready pod.
SeaweedFS keeps its existing master-1 label fallback. Preferred anti-affinity
and spreads without `minDomains > 1` do not require a second eligible node.

Capacity is an **estimate**, not a measurement of a single boot. Starting from
the supplied HA measurement (10.4 CPU / 19 GiB requests, 188 pods), subtract only
manifest-proven savings: one CNPG instance (250m/512Mi), one istiod
(100m/256Mi), and five copies each of Falco (100m/200Mi) and ztunnel
(50m/64Mi). This gives a conservative baseline-adjusted **9.30 CPU / 16.96 GiB**;
it deliberately omits further savings from two control planes, other
DaemonSets, three cert-manager replicas and five Kyverno replicas. The old
8-vCPU default cannot cover that estimate. At 10 vCPU/24 GiB, the nominal
margin is 0.70 CPU / 7.04 GiB before host services and kubelet reservations;
this does not prove the live scheduler can fit every Pod.
Offline rendering of the locally available pinned child charts found no
additional required placement across nodes. No persistent Pending Pod is
expected from the audited topology constraints after the profile is applied.
Startup PVC/image/admission waits, insufficient allocatable resources and
operator-created workloads still need a live boot to check. The chart-only subtotal is not a
whole-platform total: operator-created Pods, bootstrap-only releases,
LimitRange defaults, init/upgrade/scan Jobs and actual use can change demand.
The local pinned child-chart render subtotal is 1.835 CPU / 3.824 GiB across
44 Pods (one copy per DaemonSet, test hooks excluded); it excludes those sources
and therefore cannot replace the baseline-adjusted estimate above.
The 200-pod limit exceeds even the supplied 188-pod HA count; live allocatable
CPU/memory, Pod count and startup peaks still need checking.

Single can exercise local provisioning and service development. It cannot
verify HA/etcd quorum, kube-vip failover, control-plane or worker join, drain,
or multi-node scheduling. The idempotent `boot-heal` provisioner records topology
in root-owned `/etc/narwhal-topology` (0644). Guest verification, reboot healing,
Phase 2 and GitOps publication use it when the host record is unavailable; an
explicit contradictory environment value is refused. No live node-count inference
is used. To refresh an already-provisioned VM after updating the synced scripts:

```bash
vagrant provision master-1 --provision-with boot-heal
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh"
```

Static R342 executes the Vagrant DSL, topology resolver, staged GitOps profile
and Helm rendering without booting; it is not evidence of successful live startup.
