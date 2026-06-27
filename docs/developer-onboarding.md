# 개발자 온보딩 가이드

Narwhal IDP 클러스터에서 개발자로 작업하기 위한 가이드입니다.

---

## 1. 접속 정보

### 웹 서비스 URL

| 서비스 | URL | 용도 |
|--------|-----|------|
| ArgoCD | https://argocd.local.narwhal.io | GitOps 배포 관리 |
| Gitea | https://gitea.local.narwhal.io | Git 저장소 |
| Harbor | https://harbor.local.narwhal.io | 컨테이너 레지스트리 |
| Grafana | https://grafana.local.narwhal.io | 모니터링 대시보드 |
| Headlamp | https://headlamp.local.narwhal.io | Kubernetes UI |
| Keycloak | https://keycloak.local.narwhal.io | SSO 계정 관리 |
| OpenBao | https://openbao.local.narwhal.io | 시크릿 관리 |

### DNS 설정 (로컬 머신)

모든 `*.local.narwhal.io` 도메인은 MetalLB IP `192.168.56.200`(APISIX)으로 라우팅됩니다.
DNS는 Master 노드의 dnsmasq가 처리합니다.

**macOS:**
```bash
sudo mkdir -p /etc/resolver
printf 'nameserver 192.168.56.10\nnameserver 192.168.56.11\nnameserver 192.168.56.12\n' \
  | sudo tee /etc/resolver/local.narwhal.io

# 설정 확인
scutil --dns | grep -A3 "local.narwhal.io"
```

**Linux (systemd-resolved):**
```bash
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/narwhal.conf << 'EOF'
[Resolve]
DNS=192.168.56.10 192.168.56.11 192.168.56.12
Domains=~local.narwhal.io
EOF
sudo systemctl restart systemd-resolved
```

**간편 대안 — /etc/hosts 직접 추가:**
```
192.168.56.200 argocd.local.narwhal.io gitea.local.narwhal.io harbor.local.narwhal.io
192.168.56.200 grafana.local.narwhal.io headlamp.local.narwhal.io keycloak.local.narwhal.io
192.168.56.200 openbao.local.narwhal.io
```

> 자세한 내용: [DNS 접속 가이드](./dns-access.md)

---

## 2. 로그인 (SSO)

### 첫 로그인 절차

1. 위 URL 중 아무 곳이나 브라우저로 접속
2. APISIX openid-connect 플러그인 → Keycloak 로그인 페이지로 자동 리다이렉트
3. 관리자가 발급한 사용자명/비밀번호로 로그인
4. 로그인 후 모든 서비스에서 SSO 세션 공유 (재로그인 불필요)

> self-signed 인증서 사용으로 브라우저 보안 경고가 표시됩니다. "위험 감수 및 계속"을 클릭하세요.

### 기본 계정

| 사용자 | 비밀번호 | 역할 |
|--------|----------|------|
| `admin` | `admin` | cluster-admin |
| `dev` | `dev` | developer |
| `view` | `view` | viewer |
| `guest` | `guest` | guest (웹 UI만) |

### 역할별 접근 권한

| 역할 | ArgoCD | Gitea | Harbor | Grafana | Headlamp | kubectl |
|------|--------|-------|--------|---------|----------|---------|
| `cluster-admin` | Admin | Site Admin | Admin | Admin | 전체 | 전체 (platform-admin) |
| `developer` | 읽기+동기화 | User | Developer | Editor | dev NS edit | dev NS edit |
| `viewer` | 읽기 전용 | User (read-only) | Guest | Viewer | dev NS view | view |
| `guest` | 차단 | 차단 | 차단 | Viewer | 차단 | 없음 |

> `cluster-admin`은 노드 조작·RBAC 변경 등 위험 권한이 제거된 `platform-admin` 역할이 적용됩니다.

---

## 3. kubectl 설정

### 빠른 설정 (인증서 기반, 관리자용)

```bash
# 로컬 머신에서 실행
./scripts/common/set-config.sh cert

# 연결 확인
kubectl get nodes
```

### OIDC 기반 인증 (역할별 권한 적용)

```bash
# kubelogin 설치
brew install int128/kubelogin/kubelogin  # macOS

# OIDC kubeconfig 설정
./scripts/common/set-config.sh oidc

# 컨텍스트 전환
kubectl config use-context narwhal-oidc

# 브라우저 로그인 창이 열림 → Keycloak 계정으로 로그인
kubectl get pods -n dev
```

### 수동 토큰 발급 (스크립트/CI용)

```bash
TOKEN=$(curl -sk -X POST \
  "https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=kubernetes" \
  -d "username=dev" \
  -d "password=dev" \
  -d "scope=openid groups" \
  | jq -r '.access_token')

# X.509 인증을 우회하려면 KUBECONFIG=/dev/null 필수
KUBECONFIG=/dev/null kubectl \
  --server=https://192.168.56.100:6443 \
  --insecure-skip-tls-verify \
  --token="${TOKEN}" \
  get pods -n dev
```

### 컨텍스트 목록

```bash
kubectl config get-contexts

# 전환
kubectl config use-context narwhal       # 인증서 기반
kubectl config use-context narwhal-oidc  # OIDC 기반
kubectl config use-context narwhal-token # 서비스 계정 토큰
```

> 자세한 내용: [Kubeconfig 설정 가이드](./kubeconfig.md)

---

## 4. 앱 배포 워크플로우

### 개발자 전용 네임스페이스

`dev` 네임스페이스가 개발자 워크로드 전용으로 예약되어 있습니다.
`developer` 역할은 이 네임스페이스에서 `edit` 권한(Secrets 쓰기 포함)을 가집니다.

```bash
kubectl get pods -n dev
kubectl apply -f my-app.yaml -n dev
```

### GitOps 배포 (권장)

1. **Gitea에서 레포 생성**: https://gitea.local.narwhal.io
2. **Kubernetes 매니페스트 작성 후 push**
3. **ArgoCD Application YAML 작성**:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: devtools
spec:
  project: default
  source:
    repoURL: http://gitea-http.gitea.svc.cluster.local:3000/dev/my-app.git
    targetRevision: HEAD
    path: k8s
  destination:
    server: https://kubernetes.default.svc
    namespace: dev
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false
```

4. **App-of-Apps에 등록** (`gitops/apps/` 디렉토리에 YAML 추가 후 Gitea에 push)
5. ArgoCD가 자동 감지하여 배포

> `selfHeal: true`가 활성화된 경우 ArgoCD 외부에서 직접 수정하면 원복됩니다.

### 수동 배포 (kubectl)

```bash
kubectl apply -f my-app.yaml -n dev
kubectl rollout status deployment/my-app -n dev
```

---

## 5. Harbor 컨테이너 레지스트리

### Docker 로그인

```bash
docker login harbor.local.narwhal.io -u dev
# 비밀번호: Keycloak 계정 비밀번호 (OIDC 연동)
```

### 이미지 Push

```bash
# 이미지 태그
docker tag myapp:latest harbor.local.narwhal.io/library/myapp:latest

# Push
docker push harbor.local.narwhal.io/library/myapp:latest
```

> self-signed 인증서로 인해 Docker daemon에 insecure registry 추가가 필요할 수 있습니다.

**`/etc/docker/daemon.json`에 추가:**
```json
{
  "insecure-registries": ["harbor.local.narwhal.io"]
}
```

### Kubernetes에서 이미지 사용

```yaml
spec:
  containers:
    - name: myapp
      image: harbor.local.narwhal.io/library/myapp:latest
```

---

## 6. 모니터링 및 로그

### Grafana 대시보드

https://grafana.local.narwhal.io 접속 → Keycloak SSO 로그인

- **Kubernetes / Cluster Overview**: 클러스터 전체 상태
- **Kubernetes / Pods**: Pod별 CPU/메모리
- **Explore > Loki**: 실시간 로그 조회

### 로그 조회 (Loki)

Grafana > Explore > Loki 데이터소스 선택:

```logql
# 특정 네임스페이스의 앱 로그
{namespace="dev", app="myapp"}

# 에러만 필터
{namespace="dev"} |= "error"

# 최근 1시간
{namespace="dev", app="myapp"} | json
```

### Pod 직접 로그

```bash
kubectl logs -n dev deployment/myapp -f
kubectl logs -n dev deployment/myapp --previous  # 재시작된 컨테이너
```

---

## 7. 문제 해결

### SSO 로그인 실패

```bash
# 1. 브라우저 쿠키 삭제 (*.local.narwhal.io)

# 2. Keycloak 상태 확인
vagrant ssh master-1 -c "kubectl get pods -n iam"

# 3. APISIX 상태 확인
vagrant ssh master-1 -c "kubectl get pods -n platform-system -l app.kubernetes.io/name=apisix"

# 4. Keycloak 접근 테스트
curl -k https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration
```

### DNS 해석 실패

```bash
# DNS 서버로 직접 질의
nslookup argocd.local.narwhal.io 192.168.56.10

# dnsmasq 상태 확인 (Master 노드에서)
vagrant ssh master-1 -c "sudo systemctl status dnsmasq"
```

### Pod 문제 진단

```bash
# Pod 상태
kubectl get pods -n dev

# 상세 이벤트
kubectl describe pod <pod-name> -n dev

# 로그
kubectl logs <pod-name> -n dev

# 실행 중인 컨테이너에 접속 (developer 권한)
kubectl exec -it <pod-name> -n dev -- /bin/sh
```

### kubectl 권한 부족

```bash
# 현재 인증 정보 확인
kubectl auth whoami

# 허용된 작업 목록
kubectl auth can-i --list -n dev
```

### OIDC 토큰 캐시 문제

```bash
# kubelogin 캐시 초기화
rm -rf ~/.kube/cache/oidc-login
kubectl get pods -n dev  # 재로그인 프롬프트 표시됨
```

---

## 8. 네임스페이스 구조

| 네임스페이스 | 컴포넌트 | 개발자 접근 |
|-------------|---------|------------|
| `dev` | 개발자 워크로드 | edit (Secrets 포함) |
| `devtools` | ArgoCD, Gitea, Harbor, Headlamp | view |
| `monitoring` | Prometheus, Grafana, Loki, Tempo | view |
| `platform-system` | MetalLB, APISIX, cert-manager | view |
| `iam` | Keycloak | view |
| `storage` | OpenBao, Velero | view |

---

## 9. 참고 문서

| 문서 | 설명 |
|------|------|
| [DNS 접속 가이드](./dns-access.md) | DNS 설정 상세 및 문제 해결 |
| [Kubeconfig 설정](./kubeconfig.md) | kubectl 인증 설정 |
| [Keycloak SSO 계정](./keycloak-accounts.md) | 계정, 그룹, OIDC 클라이언트 |
| [Keycloak SSO 가이드](./keycloak-sso.md) | SSO 연동 방법 |
| [아키텍처 문서](./architecture.md) | 전체 시스템 구조 |
| [트러블슈팅](./troubleshooting.md) | 공통 문제 해결 |
