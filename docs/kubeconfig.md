# Kubeconfig 설정 가이드

로컬 머신에서 narwhal 클러스터에 접근하기 위한 kubeconfig 설정 방법입니다.

## 사전 요구사항

- narwhal 클러스터가 실행 중이어야 합니다
- `kubectl`이 설치되어 있어야 합니다
- (OIDC 사용 시) `kubelogin` 플러그인이 필요합니다

## 빠른 시작

```bash
# 기본 설정 (인증서 기반)
./scripts/common/set-config.sh

# 연결 확인
kubectl get nodes
```

## 인증 방식

### 1. 인증서 기반 (Certificate)

관리자용 접근 방식입니다. kubeadm이 생성한 admin 인증서를 사용합니다.

```bash
./scripts/common/set-config.sh cert
```

**특징:**
- 클러스터 전체 관리 권한 (cluster-admin)
- 인증서 만료 시 재설정 필요 (기본 1년)
- 개발/테스트 환경에 적합

### 2. OIDC 기반 (Keycloak)

사용자별 인증/인가가 필요한 경우 사용합니다. Keycloak을 통해 인증합니다.

```bash
# kubelogin 설치 필요
brew install int128/kubelogin/kubelogin

# OIDC 설정
./scripts/common/set-config.sh oidc

# 컨텍스트 전환
kubectl config use-context narwhal-oidc
```

**특징:**
- 사용자별 권한 관리 (RBAC)
- 그룹 기반 권한 부여
- 브라우저 기반 로그인
- 프로덕션 환경에 적합

**기본 사용자:**

| 사용자 | 비밀번호 | 그룹 | K8s 권한 |
|--------|----------|------|----------|
| admin | admin | cluster-admin | cluster-admin |
| dev | dev | developer | edit (dev NS) |
| view | view | viewer | view |
| guest | guest | guest | - (웹 UI only) |

### 3. 토큰 기반 (Service Account)

CI/CD 파이프라인이나 자동화 스크립트에서 사용합니다.

```bash
./scripts/common/set-config.sh token

# 컨텍스트 전환
kubectl config use-context narwhal-token
```

**특징:**
- 서비스 계정 토큰 사용
- 기본 유효 기간: 1년
- 자동화/CI에 적합

## 환경 변수

스크립트 실행 전 환경 변수를 설정하여 기본값을 변경할 수 있습니다.

```bash
# 클러스터 설정
export CLUSTER_NAME=narwhal      # 클러스터 이름
export MASTER_IP=192.168.56.10   # 마스터 노드 IP

# OIDC 설정 (oidc 모드에서 사용)
export OIDC_ISSUER_URL=https://keycloak.local.narwhal.internal/realms/kubernetes
export OIDC_CLIENT_ID=kubernetes

# 토큰 설정 (token 모드에서 사용)
export SA_NAME=admin-user        # 서비스 계정 이름
export SA_NAMESPACE=kube-system  # 서비스 계정 네임스페이스
```

## 컨텍스트 관리

```bash
# 현재 컨텍스트 확인
kubectl config current-context

# 사용 가능한 컨텍스트 목록
kubectl config get-contexts

# 컨텍스트 전환
kubectl config use-context narwhal       # 인증서 기반
kubectl config use-context narwhal-oidc  # OIDC 기반
kubectl config use-context narwhal-token # 토큰 기반

# 컨텍스트 삭제
kubectl config delete-context narwhal
```

## kubelogin 설치

OIDC 인증을 사용하려면 kubelogin 플러그인이 필요합니다.

### macOS

```bash
brew install int128/kubelogin/kubelogin
```

### Linux

```bash
# Go로 설치
go install github.com/int128/kubelogin/cmd/kubelogin@latest

# 또는 바이너리 직접 다운로드
curl -LO https://github.com/int128/kubelogin/releases/latest/download/kubelogin_linux_amd64.zip
unzip kubelogin_linux_amd64.zip
sudo mv kubelogin /usr/local/bin/kubectl-oidc_login
```

### Windows

```powershell
# Chocolatey
choco install kubelogin

# 또는 Scoop
scoop install kubelogin
```

## 문제 해결

### 연결 거부 (Connection refused)

```bash
# 클러스터 상태 확인
vagrant status

# 클러스터 시작
vagrant up master-1
```

### 인증서 오류

```bash
# kubeconfig 재설정
./scripts/common/set-config.sh cert
```

### OIDC 로그인 실패

1. Keycloak이 실행 중인지 확인:
   ```bash
   kubectl get pods -n iam
   ```

2. Keycloak 접근 테스트:
   ```bash
   curl -k https://keycloak.local.narwhal.internal/realms/kubernetes/.well-known/openid-configuration
   ```

3. kubelogin 캐시 삭제:
   ```bash
   rm -rf ~/.kube/cache/oidc-login
   ```

### 권한 부족 (Forbidden)

OIDC 사용자의 권한 확인:
```bash
# 현재 사용자 확인
kubectl auth whoami

# 권한 확인
kubectl auth can-i --list
```

## 참고

- [Kubernetes Authentication](https://kubernetes.io/docs/reference/access-authn-authz/authentication/)
- [kubelogin](https://github.com/int128/kubelogin)
- [Keycloak SSO 가이드](./keycloak-sso.md)
