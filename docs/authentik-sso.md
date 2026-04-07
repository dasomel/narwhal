# Authentik SSO 가이드

Narwhal IDP 플랫폼의 Authentik SSO 연동 가이드

---

## 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│               Authentik (IAM / SSO)                         │
│         https://authentik.local.narwhal.io                  │
├─────────────────────────────────────────────────────────────┤
│  Groups            │  OIDC Providers               │        │
│  ─────────         │  ────────────                 │        │
│  cluster-admin     │  kubernetes (public)           │        │
│  developer         │  apisix     (confidential)     │        │
│  viewer            │                                │        │
│  guest             │                                │        │
└─────────────────────────────────────────────────────────────┘
          │                         │
          ▼                         ▼
┌─────────────────┐    ┌──────────────────────────────────────┐
│  K8s API Server │    │  APISIX Gateway  (192.168.56.200)    │
│  (--oidc-*)     │    │  openid-connect plugin               │
│  kubectl login  │    │  → 모든 보호 라우트에 SSO 적용        │
└─────────────────┘    └──────────────────────────────────────┘
                                    │
               ┌────────────────────┼────────────────────┐
               ▼                    ▼                    ▼
           ArgoCD               Grafana              Headlamp
           Gitea                Harbor               Velero UI
           Prometheus           Alertmanager         ...
```

### 핵심 설계 원칙

- **단일 confidential provider (`apisix`)**: APISIX gateway가 모든 웹 앱의 SSO 진입점
  - 앱별 OIDC 클라이언트 등록 불필요 (Grafana/Harbor/Gitea 등은 앱 자체 OIDC로도 연동 가능하나 `apisix` provider 재사용)
- **public provider (`kubernetes`)**: kubectl OIDC 로그인용 (로컬 callback 허용)
- **groups scope**: Python expression으로 사용자 그룹 목록을 `groups` claim에 포함

---

## 1. Authentik 설정 구조

### 1.1 OAuth2 Providers

| Provider | Client Type | Client ID | Issuer URL | 용도 |
|----------|-------------|-----------|------------|------|
| `kubernetes` | Public | `kubernetes` | `https://authentik.local.narwhal.io/application/o/kubernetes/` | K8s API Server OIDC, kubectl 로그인 |
| `apisix` | Confidential | `apisix` | `https://authentik.local.narwhal.io/application/o/apisix/` | APISIX gateway를 통한 웹 앱 SSO 전체 |

### 1.2 Applications (Slug)

| Application | Slug | Provider | 매핑되는 Issuer URL |
|-------------|------|----------|---------------------|
| kubernetes | `kubernetes` | kubernetes provider | `.../application/o/kubernetes/` |
| apisix | `apisix` | apisix provider | `.../application/o/apisix/` |

> **중요**: OIDC issuer URL은 application slug로 결정됩니다.
> `https://authentik.local.narwhal.io/application/o/{slug}/`

### 1.3 Property Mapping (Groups Claim)

Authentik 기본 scope에는 `groups` claim이 없으므로 별도 Scope Mapping 생성:

| 이름 | Scope Name | Expression |
|------|------------|------------|
| `groups` | `groups` | `return [group.name for group in request.user.ak_groups.all()]` |

---

## 2. 그룹 및 사용자

### 2.1 그룹

| Group | K8s 권한 | 앱 권한 |
|-------|----------|---------|
| `cluster-admin` | cluster-admin | 모든 앱 Admin |
| `developer` | edit (dev NS) | 앱별 editor/developer |
| `viewer` | view | 앱별 viewer/read-only |
| `guest` | 없음 | 웹 UI만 (일부 앱 접근 제한) |

### 2.2 기본 사용자

비밀번호는 프로비저닝 시 자동 생성되어 K8s Secret에 저장됩니다.

```bash
# 비밀번호 확인
kubectl get secret authentik-user-passwords -n iam -o jsonpath='{.data.admin}' | base64 -d   # admin
kubectl get secret authentik-user-passwords -n iam -o jsonpath='{.data.dev}' | base64 -d     # dev
kubectl get secret authentik-user-passwords -n iam -o jsonpath='{.data.view}' | base64 -d    # view
kubectl get secret authentik-user-passwords -n iam -o jsonpath='{.data.guest}' | base64 -d   # guest

# Authentik bootstrap admin 비밀번호
kubectl get secret authentik-bootstrap-secret -n iam -o jsonpath='{.data.bootstrap_password}' | base64 -d
```

| Username | Email | Group |
|----------|-------|-------|
| `admin` | admin@local.narwhal.io | cluster-admin |
| `dev` | dev@local.narwhal.io | developer |
| `view` | view@local.narwhal.io | viewer |
| `guest` | guest@local.narwhal.io | guest |

---

## 3. 앱별 SSO 연동

### 3.1 APISIX Gateway (공통 SSO 진입점)

모든 보호 라우트에 `openid-connect` 플러그인이 적용됩니다.

**OIDC 설정** (`apisix-oidc-config` 시크릿):
```bash
kubectl get secret apisix-oidc-config -n platform-system -o yaml
```

| 항목 | 값 |
|------|----|
| Client ID | `apisix` |
| Client Secret | `apisix-oidc-config` Secret의 `client_secret` |
| Issuer URL | `https://authentik.local.narwhal.io/application/o/apisix/` |
| Scopes | `openid profile email groups` |
| Callback Path | `/apisix/callback` |
| Redirect URI | `https://{host}.local.narwhal.io/apisix/callback` |

**라우트별 SSO 적용 여부**:

| 도메인 | SSO | 비고 |
|--------|-----|------|
| `argocd.local.narwhal.io` | ✅ | ArgoCD 자체 OIDC 연동도 별도 구성 |
| `grafana.local.narwhal.io` | ✅ | Grafana 자체 generic_oauth |
| `gitea.local.narwhal.io` | ✅ | Gitea 자체 OAuth2 |
| `harbor.local.narwhal.io` | ✅ | Harbor 자체 OIDC |
| `headlamp.local.narwhal.io` | ✅ | Headlamp 자체 OIDC |
| `prometheus.local.narwhal.io` | ✅ | APISIX-only (Prometheus 자체 SSO 없음) |
| `alertmanager.local.narwhal.io` | ✅ | APISIX-only |
| `hubble.local.narwhal.io` | ✅ | APISIX-only |
| `authentik.local.narwhal.io` | ❌ | Authentik 자체 |

---

### 3.2 ArgoCD

**연동 방식**: ArgoCD 자체 OIDC 설정 (`argocd-cm`)

| 항목 | 값 |
|------|----|
| Provider | `apisix` (confidential) |
| Client ID | `apisix` |
| Issuer URL | `https://authentik.local.narwhal.io/application/o/apisix/` |
| Scopes | `openid profile email groups` |

**RBAC 정책** (`argocd-rbac-cm`):

```csv
g, cluster-admin, role:admin
g, developer, role:developer
g, viewer, role:readonly
p, role:developer, applications, sync, */*, allow
p, role:developer, applications, get, */*, allow
p, role:developer, logs, get, */*, allow
```

| Authentik Group | ArgoCD Role | 권한 |
|-----------------|-------------|------|
| `cluster-admin` | `role:admin` | 모든 앱/프로젝트/클러스터 관리 |
| `developer` | `role:developer` | 앱 sync, 로그 조회 |
| `viewer` | `role:readonly` | 읽기 전용 |

**Redirect URI 등록** (Authentik `apisix` provider):
```
https://argocd.local.narwhal.io/auth/callback
```

---

### 3.3 Grafana

**연동 방식**: `auth.generic_oauth` (자체 OIDC)

| 항목 | 값 |
|------|----|
| Provider | `apisix` (confidential) |
| Client ID | `apisix` |
| Client Secret | `grafana-oauth-secret` Secret의 `client_secret` |
| Auth URL | `https://authentik.local.narwhal.io/application/o/apisix/authorize/` |
| Token URL | `https://authentik.local.narwhal.io/application/o/token/` |
| API URL | `https://authentik.local.narwhal.io/application/o/userinfo/` |
| TLS | `tls_skip_verify_insecure: true` (self-signed CA) |

**Role 매핑** (JMESPath):
```
contains(groups[*], 'cluster-admin') && 'Admin' || contains(groups[*], 'developer') && 'Editor' || 'Viewer'
```

| Authentik Group | Grafana Role | 권한 |
|-----------------|--------------|------|
| `cluster-admin` | `Admin` | 사용자/데이터소스/대시보드 전체 관리 |
| `developer` | `Editor` | 대시보드 생성/수정 |
| `viewer` / `guest` | `Viewer` | 대시보드 조회 |

**Secret 확인**:
```bash
kubectl get secret grafana-oauth-secret -n monitoring -o jsonpath='{.data.client_secret}' | base64 -d
```

---

### 3.4 Gitea

**연동 방식**: Gitea Admin Console에서 OAuth2 소스 추가 또는 Helm values

| 항목 | 값 |
|------|----|
| Provider | `apisix` (confidential) |
| Client ID | `apisix` |
| Auth URL | `https://authentik.local.narwhal.io/application/o/apisix/authorize/` |
| Token URL | `https://authentik.local.narwhal.io/application/o/token/` |
| Scopes | `openid profile email groups` |
| Admin Group | `cluster-admin` |

**Gitea Admin Console 설정 경로**:
```
Site Admin → Authentication Sources → Add 인증 소스
→ OAuth2 선택 → "Custom" 선택 → 위 값 입력
```

| Authentik Group | Gitea 역할 | 권한 |
|-----------------|-----------|------|
| `cluster-admin` | Site Admin | 전체 관리자 |
| `developer` | User | 저장소 생성/관리 |
| `viewer` | User (read-only) | 공개 저장소 접근 |

**Redirect URI 등록**:
```
https://gitea.local.narwhal.io/user/oauth2/{source-name}/callback
```

---

### 3.5 Harbor

**연동 방식**: Harbor 자체 OIDC Auth Mode (`configureUserSettings`)

| 항목 | 값 |
|------|----|
| Provider | `apisix` (confidential) |
| OIDC Name | `Authentik` |
| OIDC Endpoint | `https://authentik.local.narwhal.io/application/o/apisix/` |
| Client ID | `apisix` |
| Groups Claim | `groups` |
| Admin Group | `cluster-admin` |
| Auto Onboard | `true` |
| Verify Cert | `false` (self-signed) |

**Client Secret 주입** (런타임, 스크립트로 패치):
```bash
# Harbor OIDC client_secret은 Helm values에 직접 못 넣으므로 kubectl patch로 주입
APISIX_SECRET=$(kubectl get secret apisix-oidc-config -n platform-system \
  -o jsonpath='{.data.client_secret}' | base64 -d)
kubectl patch deployment harbor-core -n devtools --type=json \
  -p="[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",
  \"value\":{\"name\":\"CONFIG_OVERWRITE_JSON_OidcClientSecret\",\"value\":\"${APISIX_SECRET}\"}}]"
```

| Authentik Group | Harbor 역할 | 권한 |
|-----------------|-------------|------|
| `cluster-admin` | Admin | 전체 관리자 |
| `developer` | Developer | 이미지 Push/Pull, 스캔 |
| `viewer` | Guest | 이미지 Pull만 |

> **주의**: Harbor는 OIDC Auth Mode 활성화 후 로컬 로그인이 비활성화됩니다.
> admin 계정 잠금 시 `HARBOR_ADMIN_PASSWORD` 환경변수 또는 DB 직접 수정 필요.

---

### 3.6 Headlamp (Kubernetes Dashboard)

**연동 방식**: Headlamp 자체 OIDC

| 항목 | 값 |
|------|----|
| Provider | `apisix` (confidential) |
| Client ID | `apisix` |
| Issuer URL | `https://authentik.local.narwhal.io/application/o/apisix/` |
| Scopes | `openid profile email groups` |
| TLS | narwhal-ca cert bundle 마운트 (CA 신뢰) |

**Secret 확인**:
```bash
kubectl get secret headlamp-oidc-secret -n devtools -o yaml
```

**K8s 권한**: Headlamp은 K8s RBAC을 직접 사용하므로 Authentik 그룹 → K8s Group 매핑을 따름.

| Authentik Group | K8s Group (OIDC prefix) | 권한 |
|-----------------|-------------------------|------|
| `cluster-admin` | `oidc:cluster-admin` | ClusterRole: cluster-admin |
| `developer` | `oidc:developer` | Role: edit (dev NS) |
| `viewer` | `oidc:viewer` | Role: view |

---

### 3.7 K8s API Server (kubectl OIDC 로그인)

**연동 방식**: kube-apiserver `--oidc-*` 플래그

| 항목 | 값 |
|------|----|
| Provider | `kubernetes` (public) |
| Issuer URL | `https://authentik.local.narwhal.io/application/o/kubernetes/` |
| Client ID | `kubernetes` |
| Username Claim | `preferred_username` |
| Groups Claim | `groups` |
| Username Prefix | `oidc:` |
| Groups Prefix | `oidc:` |

**kube-apiserver 설정**:
```yaml
# /etc/kubernetes/manifests/kube-apiserver.yaml
- --oidc-issuer-url=https://authentik.local.narwhal.io/application/o/kubernetes/
- --oidc-client-id=kubernetes
- --oidc-username-claim=preferred_username
- --oidc-groups-claim=groups
- --oidc-username-prefix=oidc:
- --oidc-groups-prefix=oidc:
- --oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt
```

> ⚠️ **K8s 1.35 이상**: OIDC Issuer URL은 반드시 **HTTPS** 여야 합니다.

**kubectl OIDC 로그인 설정**:
```bash
# kubelogin 플러그인 설치
brew install int128/kubelogin/kubelogin

# kubeconfig 설정
kubectl config set-credentials oidc \
  --exec-api-version=client.authentication.k8s.io/v1beta1 \
  --exec-command=kubectl \
  --exec-arg=oidc-login \
  --exec-arg=get-token \
  --exec-arg=--oidc-issuer-url=https://authentik.local.narwhal.io/application/o/kubernetes/ \
  --exec-arg=--oidc-client-id=kubernetes \
  --exec-arg=--insecure-skip-tls-verify

# 테스트
kubectl --user=oidc get nodes
```

---

## 4. K8s RBAC 매핑

### 4.1 ClusterRoleBindings

```yaml
# cluster-admin 그룹 → K8s cluster-admin
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-cluster-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: "oidc:cluster-admin"

---
# developer 그룹 → dev 네임스페이스 edit
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-developer-edit
  namespace: dev
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: edit
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: "oidc:developer"

---
# viewer 그룹 → 읽기 전용 (dev, devtools, monitoring)
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-viewer
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: "oidc:viewer"
```

### 4.2 전체 권한 매트릭스

| Group | K8s API | ArgoCD | Grafana | Gitea | Harbor | Headlamp |
|-------|:-------:|:------:|:-------:|:-----:|:------:|:--------:|
| **cluster-admin** | cluster-admin | Admin | Admin | Site Admin | Admin | Full |
| **developer** | edit (dev NS) | Developer | Editor | User | Developer | NS edit |
| **viewer** | view | Readonly | Viewer | Read | Guest | view |
| **guest** | ❌ | ❌ | Viewer | ❌ | ❌ | ❌ |

---

## 5. K8s Secrets 구조

| Secret 이름 | Namespace | 포함 키 | 용도 |
|------------|-----------|---------|------|
| `authentik-bootstrap-secret` | `iam` | `bootstrap_token`, `bootstrap_password` | Authentik API 및 초기 어드민 |
| `authentik-user-passwords` | `iam` | `admin`, `dev`, `view`, `guest` | 사용자 비밀번호 |
| `apisix-oidc-config` | `platform-system` | `client_id`, `client_secret`, `session_secret` | APISIX openid-connect 플러그인 |
| `grafana-oauth-secret` | `monitoring` | `client_secret` | Grafana generic_oauth |
| `headlamp-oidc-secret` | `devtools` | `clientID`, `clientSecret`, `issuerURL`, `scopes` | Headlamp OIDC |
| `velero-ui-oauth` | `storage` | `client_secret`, `passphrase` | Velero UI OAuth |

---

## 6. Authentik Admin UI에서 수동 등록

자동화 스크립트(`11-2-authentik-config.sh`)로 기본 설정이 완료되지만,
특정 앱을 위해 **별도 Application/Provider를 추가**해야 할 경우 아래 절차를 따릅니다.

### 6.1 새 OAuth2 Provider 등록

1. `https://authentik.local.narwhal.io` 접속 → Admin Interface
2. **Providers** → Create → `OAuth2/OpenID Provider`
3. 입력 항목:

| 항목 | 값 |
|------|----|
| Name | `{앱이름}` |
| Authorization Flow | `default-provider-authorization-implicit-consent` |
| Client Type | `Confidential` (서버사이드 앱) 또는 `Public` (SPA/CLI) |
| Client ID | `{앱이름}` (자동생성 또는 직접 입력) |
| Redirect URIs | `https://{앱 도메인}/callback` 또는 `regex: .*` |
| Scopes | `openid`, `profile`, `email`, `groups` (커스텀) 체크 |
| Subject Mode | `Based on the Hashed User ID` 또는 `Based on the Username` |

### 6.2 새 Application 등록

1. **Applications** → Create
2. 입력 항목:

| 항목 | 값 |
|------|----|
| Name | `{앱이름}` |
| Slug | `{앱이름}` → **issuer URL 결정**: `.../application/o/{slug}/` |
| Provider | 위에서 생성한 Provider 선택 |

<details>
<summary>Authentik OIDC Endpoints 패턴</summary>

```
# {slug} = Application slug
Issuer URL      : https://authentik.local.narwhal.io/application/o/{slug}/
Authorization   : https://authentik.local.narwhal.io/application/o/{slug}/authorize/
Token           : https://authentik.local.narwhal.io/application/o/token/
UserInfo        : https://authentik.local.narwhal.io/application/o/userinfo/
JWKS            : https://authentik.local.narwhal.io/application/o/{slug}/jwks/
End Session     : https://authentik.local.narwhal.io/application/o/{slug}/end-session/
Discovery (.well-known): https://authentik.local.narwhal.io/application/o/{slug}/.well-known/openid-configuration
```

</details>

### 6.3 groups Scope Mapping 확인

**Customization → Property Mappings → Scope Mappings** 에서 `groups` scope 존재 확인:

```python
# Expression
return [group.name for group in request.user.ak_groups.all()]
```

없으면 직접 생성:
- Name: `groups`
- Scope Name: `groups`
- Expression: 위 코드

생성 후 Provider의 **Scopes** 목록에 `groups` 추가.

---

## 7. 진단 및 트러블슈팅

### 7.1 OIDC Discovery 확인

```bash
# apisix provider
curl -sk https://authentik.local.narwhal.io/application/o/apisix/.well-known/openid-configuration | jq

# kubernetes provider
curl -sk https://authentik.local.narwhal.io/application/o/kubernetes/.well-known/openid-configuration | jq
```

### 7.2 토큰 발급 테스트

```bash
# apisix provider (confidential)
APISIX_SECRET=$(kubectl get secret apisix-oidc-config -n platform-system \
  -o jsonpath='{.data.client_secret}' | base64 -d)

curl -sk -X POST \
  "https://authentik.local.narwhal.io/application/o/token/" \
  -d "grant_type=password" \
  -d "client_id=apisix" \
  -d "client_secret=${APISIX_SECRET}" \
  -d "username=admin" \
  -d "password=$(kubectl get secret authentik-user-passwords -n iam -o jsonpath='{.data.admin}' | base64 -d)" \
  -d "scope=openid profile email groups" | jq

# JWT 디코딩 (groups claim 확인)
TOKEN=$(curl -sk -X POST "https://authentik.local.narwhal.io/application/o/token/" \
  -d "grant_type=password&client_id=apisix&client_secret=${APISIX_SECRET}&username=admin&password=$(kubectl get secret authentik-user-passwords -n iam -o jsonpath='{.data.admin}' | base64 -d)&scope=openid profile email groups" \
  | jq -r '.access_token')
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq '{groups, preferred_username, sub}'
```

### 7.3 APISIX SSO 테스트

```bash
# APISIX OIDC 플러그인 상태 확인
curl -sk http://localhost:9180/apisix/admin/routes \
  -H "X-API-KEY: $(kubectl get secret apisix-admin-secret -n platform-system -o jsonpath='{.data.key}' | base64 -d)" \
  | jq '.list[].value.plugins."openid-connect"'
```

### 7.4 K8s RBAC 확인

```bash
# 특정 그룹 권한 확인
kubectl auth can-i --list --as=dev --as-group=oidc:developer
kubectl auth can-i --list --as=admin --as-group=oidc:cluster-admin

# OIDC ClusterRoleBinding 확인
kubectl get clusterrolebinding oidc-cluster-admin -o yaml
```

### 7.5 일반적인 오류

| 오류 | 원인 | 해결 |
|------|------|------|
| `groups` claim 없음 | Scope mapping 미등록 또는 Provider에 미포함 | Authentik Admin → Property Mappings → Provider에 `groups` 추가 |
| `invalid_client` | Client Secret 틀림 | `apisix-oidc-config` Secret 재확인, Provider 재생성 고려 |
| `redirect_uri` 불일치 | Provider의 Redirect URI 패턴 불일치 | Authentik Provider에서 허용 URI 추가 또는 regex `.*`로 변경 |
| TLS 인증서 오류 | self-signed CA 미신뢰 | narwhal-ca cert 마운트 또는 `tls_skip_verify_insecure: true` |
| OIDC Issuer URL 불일치 | application slug 잘못됨 | `.well-known/openid-configuration`의 `issuer` 필드 확인 |
| kube-apiserver crash | OIDC issuer HTTP 사용 (K8s 1.35+) | Issuer URL을 HTTPS로 변경 |
| Authentik 접근 불가 (192.168.56.200) | MetalLB / APISIX 장애 | `troubleshooting.md` #14 섹션 참조 |

---

## 8. 재설정 절차

### 8.1 OIDC Secret 재생성

```bash
# 11-2-authentik-config.sh 재실행
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-2-authentik-config.sh"
```

### 8.2 Authentik Provider 수동 재설정

1. Authentik Admin UI → Providers → 해당 Provider 삭제
2. Applications → 해당 Application 삭제
3. `11-2-authentik-config.sh` 재실행

### 8.3 사용자 비밀번호 재설정

```bash
# K8s Secret에서 현재 비밀번호 확인
kubectl get secret authentik-user-passwords -n iam -o go-template='
{{- range $k, $v := .data}}{{ $k }}: {{ $v | base64decode }}
{{end}}'
```

---

## 관련 문서

- [`architecture.md`](./architecture.md) - 아키텍처 개요
- [`authentik-accounts.md`](./authentik-accounts.md) - Authentik 계정 관리 상세
- [`dns-access.md`](./dns-access.md) - DNS 및 접근 방법
- [`troubleshooting.md`](./troubleshooting.md) - 트러블슈팅 가이드
- [`operations.md`](./operations.md) - 운영 가이드
