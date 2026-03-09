# Keycloak SSO & Authorization Guide

Narwhal IDP 플랫폼의 Keycloak SSO 연동 및 권한 관리 가이드

## IMPORTANT: Kubernetes 1.35+ HTTPS Requirement

**K8s 1.35부터 OIDC Issuer URL은 반드시 HTTPS를 사용해야 합니다.**

### 주요 변경사항

- K8s 1.35는 `--oidc-*` 플래그를 내부적으로 `StructuredAuthenticationConfiguration`로 변환
- **HTTP issuer URL 사용 시 API 서버가 크래시**하며 다음 오류 발생:
  ```
  jwt[0].issuer.url: Invalid value: "http://...": URL scheme must be https
  ```
- **해결책**: cert-manager TLS가 설치된 후에만 OIDC를 활성화해야 함

### 설치 순서 (필수)

```
08-1-networking.sh          → cert-manager + Traefik TLS 설치
09-istio-ambient.sh         → Istio ambient mesh (mTLS)
10-dnsmasq.sh               → DNS 설정 (*.local.narwhal.io 해석)
11-1-keycloak-operator.sh   → Keycloak Operator 설치
11-2-keycloak-realm.sh      → Realm 및 사용자 설정
11-3-keycloak-clients.sh    → OIDC 클라이언트 설정
11-4-keycloak-apiserver.sh  → K8s API 서버 OIDC 연동
```

### 검증 방법

```bash
# Keycloak HTTPS 엔드포인트 확인 (self-signed cert이므로 -k 플래그 필수)
curl -k https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration

# API 서버 OIDC 설정 확인
kubectl -n kube-system get pod kube-apiserver-* -o yaml | grep oidc-issuer-url
# 출력: --oidc-issuer-url=https://keycloak.local.narwhal.io/realms/kubernetes
```

---

## 1. Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Keycloak (IAM/SSO)                         │
│                    Realm: kubernetes                            │
├─────────────────────────────────────────────────────────────────┤
│  Groups          │  Realm Roles      │  OIDC Clients            │
│  ───────────     │  ───────────      │  ───────────             │
│  cluster-admin   │  cluster-admin    │  kubernetes (K8s API)    │
│  developer       │  developer        │  argocd                  │
│  viewer          │  viewer           │  gitea                   │
│  guest           │  -                │  harbor                  │
│                  │                   │  headlamp                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Applications                                 │
│  K8s API │ ArgoCD │ Grafana │ Gitea │ Harbor │ Headlamp         │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Keycloak Configuration

### 2.1 Realm

| Item | Value |
|------|-------|
| Realm Name | `kubernetes` |
| SSL Required | none (dev) / external (prod) |
| Login Theme | keycloak |

### 2.2 Groups

| Group | Description | Realm Roles |
|-------|-------------|-------------|
| `cluster-admin` | 클러스터 전체 관리자 | cluster-admin |
| `developer` | 개발자 (dev NS edit, devtools/monitoring view) | developer |
| `viewer` | 읽기 전용 (dev/devtools/monitoring view) | viewer |
| `guest` | 웹 UI 전용 (K8s 접근 없음) | - |

### 2.3 Realm Roles

| Role | Description |
|------|-------------|
| `cluster-admin` | 클러스터 전체 관리 권한 |
| `developer` | 리소스 생성/수정/삭제 권한 |
| `viewer` | 리소스 조회 권한만 |

### 2.4 Default Users

| Username | Password | Email | Group |
|----------|----------|-------|-------|
| `admin` | `admin` | admin@local | cluster-admin |
| `dev` | `dev` | dev@local | developer |
| `view` | `view` | view@local | viewer |
| `guest` | `guest` | guest@local | guest |

## 3. OIDC Clients

### 3.1 Client Configuration

| Client ID | Type | Secret | Redirect URIs |
|-----------|------|--------|---------------|
| `kubernetes` | Public | - | `*` |
| `argocd` | Confidential | `argocd-secret` | `https://argocd.local.narwhal.io/*`, `http://localhost:8443/*` |
| `grafana` | Confidential | `grafana-secret` | `https://grafana.local.narwhal.io/*`, `http://localhost:3000/*` |
| `gitea` | Confidential | `gitea-secret` | `https://gitea.local.narwhal.io/*`, `http://localhost:3000/*` |
| `harbor` | Confidential | `harbor-secret` | `https://harbor.local.narwhal.io/*`, `http://localhost:8080/*` |
| `headlamp` | Confidential | `headlamp-secret` | `https://headlamp.local.narwhal.io/*`, `http://localhost:8080/*` |
| `oauth2-proxy` | Confidential | `oauth2-proxy-secret` | `https://oauth2.local.narwhal.io/oauth2/callback` |

### 3.2 Client Scopes (IMPORTANT)

**`groups` client scope은 반드시 REALM-LEVEL scope으로 생성해야 합니다.**

개별 클라이언트의 mapper가 아닌, realm 전체에서 공유되는 client scope로 설정:

1. **Realm-level Client Scope 생성**
   - Keycloak Admin Console → Client Scopes → Create
   - Name: `groups`
   - Protocol: `openid-connect`
   - Include in token scope: `true`

2. **Mapper 추가**
   ```json
   {
     "name": "oidc-group-membership-mapper",
     "protocol": "openid-connect",
     "protocolMapper": "oidc-group-membership-mapper",
     "config": {
       "claim.name": "groups",
       "full.path": "false",
       "id.token.claim": "true",
       "access.token.claim": "true",
       "userinfo.token.claim": "true"
     }
   }
   ```

3. **모든 클라이언트에 Default Scope으로 할당**
   - 각 클라이언트 → Client Scopes 탭 → Add client scope
   - `groups` scope을 **Default** 타입으로 추가

**주의**: `groups` scope이 없는 상태에서 요청하면 `invalid_scope` 에러 발생

### 3.3 OIDC Endpoints

| Endpoint | URL |
|----------|-----|
| Issuer | `https://keycloak.local.narwhal.io/realms/kubernetes` |
| Authorization | `{issuer}/protocol/openid-connect/auth` |
| Token | `{issuer}/protocol/openid-connect/token` |
| UserInfo | `{issuer}/protocol/openid-connect/userinfo` |
| JWKS | `{issuer}/protocol/openid-connect/certs` |
| Discovery | `{issuer}/.well-known/openid-configuration` |

**클러스터 내부 통신**: `http://keycloak-service.iam.svc.cluster.local:8080/realms/kubernetes`   
**외부/API Server**: `https://keycloak.local.narwhal.io/realms/kubernetes` (HTTPS 필수)

## 4. Kubernetes OIDC Integration

### 4.1 API Server OIDC Configuration

`kubeadm-config.yaml`:
```yaml
apiServer:
  extraArgs:
    oidc-issuer-url: "https://keycloak.local.narwhal.io/realms/kubernetes"
    oidc-client-id: "kubernetes"
    oidc-username-claim: "preferred_username"
    oidc-username-prefix: "oidc:"
    oidc-groups-claim: "groups"
    oidc-groups-prefix: "oidc:"
```

**중요**: K8s 1.35+에서는 HTTPS issuer URL이 필수입니다. HTTP 사용 시 API 서버가 시작되지 않습니다.

### 4.2 RBAC Mapping

| Keycloak Group | K8s Group (prefixed) | ClusterRole | Permissions |
|----------------|---------------------|-------------|-------------|
| `cluster-admin` | `oidc:cluster-admin` | `cluster-admin` | 전체 클러스터 관리 |
| `developer` | `oidc:developer` | `edit` (dev NS), `view` (devtools, monitoring) | 네임스페이스별 권한 |
| `viewer` | `oidc:viewer` | `view` (dev, devtools, monitoring) | 리소스 읽기 전용 |
| `guest` | - | - | K8s 접근 없음 (웹 UI OIDC만) |

### 4.3 ClusterRoleBindings

```yaml
# cluster-admin → cluster-admin (ClusterRoleBinding, full access)
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
  name: oidc:cluster-admin

---
# developer → edit (RoleBinding, dev NS only)
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
  name: oidc:developer

---
# developer → view (RoleBinding, devtools/monitoring)
# Applied to: devtools, monitoring namespaces

---
# viewer → view (RoleBinding, dev/devtools/monitoring)
# Applied to: dev, devtools, monitoring namespaces
```

### 4.4 K8s Built-in ClusterRoles

| ClusterRole | Description |
|-------------|-------------|
| `cluster-admin` | 모든 리소스에 대한 모든 권한 |
| `admin` | 네임스페이스 내 모든 권한 (RBAC 제외) |
| `edit` | 리소스 생성/수정/삭제 (RBAC, 네임스페이스 제외) |
| `view` | 대부분의 리소스 읽기 (Secrets 제외) |

## 5. Application Authorization

### 5.1 ArgoCD

**RBAC Policy (`argocd-rbac-cm`):**
```csv
p, role:developer, applications, sync, */*, allow
p, role:developer, applications, get, */*, allow
p, role:developer, logs, get, */*, allow
g, cluster-admin, role:admin
g, developer, role:developer
g, viewer, role:readonly
```

| Keycloak Group | ArgoCD Role | Permissions |
|----------------|-------------|-------------|
| `cluster-admin` | `role:admin` | 모든 앱/프로젝트 관리 |
| `developer` | `role:developer` | 앱 sync/get, 로그 조회 |
| `viewer` | `role:readonly` | 읽기 전용 |

**ArgoCD Built-in Roles:**

| Role | Permissions |
|------|-------------|
| `role:admin` | 모든 권한 (앱, 클러스터, 프로젝트, 설정) |
| `role:developer` | 앱 sync, 로그 조회, exec |
| `role:readonly` | 앱/리소스 조회만 |

### 5.2 Grafana

**Role Mapping (JMESPath):**
```
contains(groups[*], 'cluster-admin') && 'Admin' ||
contains(groups[*], 'developer') && 'Editor' ||
'Viewer'
```

| Keycloak Group | Grafana Role | Permissions |
|----------------|--------------|-------------|
| `cluster-admin` | `Admin` | 사용자/데이터소스/대시보드 관리 |
| `developer` | `Editor` | 대시보드 생성/수정 |
| `viewer` | `Viewer` | 대시보드 조회만 |
| `guest` | `Viewer` | 대시보드 조회만 |

**Grafana Built-in Roles:**

| Role | Permissions |
|------|-------------|
| `Admin` | 조직 설정, 사용자 관리, 데이터소스 관리 |
| `Editor` | 대시보드/폴더 생성/수정, 알림 규칙 관리 |
| `Viewer` | 대시보드 조회, 탐색 |

### 5.3 Gitea

**OIDC Configuration:**
```
admin-group: cluster-admin
```

| Keycloak Group | Gitea Role | Permissions |
|----------------|------------|-------------|
| `cluster-admin` | Site Admin | 전체 관리자 |
| `developer` | User | 저장소 생성/관리 |
| `viewer` | User (read-only) | 공개 저장소 접근 |
| `guest` | - | 접근 불가 |

**Gitea Access Levels:**

| Level | Permissions |
|-------|-------------|
| Site Admin | 사용자/조직/저장소 전체 관리 |
| Organization Owner | 조직 내 모든 권한 |
| Team Admin | 팀 관리 |
| Write | Push, 브랜치 생성 |
| Read | Clone, Pull |

### 5.4 Harbor

**OIDC Configuration:**
```json
{
  "oidc_admin_group": "cluster-admin",
  "oidc_groups_claim": "groups"
}
```

| Keycloak Group | Harbor Role | Permissions |
|----------------|-------------|-------------|
| `cluster-admin` | Admin | 전체 관리자 |
| `developer` | Developer | 이미지 Push/Pull |
| `viewer` | Guest | 이미지 Pull만 |
| `guest` | - | 접근 불가 |

**Harbor Project Roles:**

| Role | Permissions |
|------|-------------|
| Project Admin | 프로젝트 설정, 멤버 관리 |
| Maintainer | 이미지 Push/Pull, 스캔, 삭제 |
| Developer | 이미지 Push/Pull, 스캔 |
| Guest | 이미지 Pull만 |
| Limited Guest | 로그 조회만 |

### 5.5 Headlamp

**OIDC Scopes:**
```
openid, profile, email, groups
```

| Keycloak Group | Headlamp Access | K8s Permissions |
|----------------|-----------------|-----------------|
| `cluster-admin` | Full Access | cluster-admin |
| `developer` | Namespace Access | dev NS edit |
| `viewer` | Read Only | dev NS view |
| `guest` | - | 접근 불가 |

Headlamp은 K8s RBAC을 직접 사용하므로, Keycloak 그룹 → K8s ClusterRole 매핑을 따릅니다.

## 6. Permission Matrix

### 6.1 Overall Permission Matrix

| Group | Kubernetes | ArgoCD | Grafana | Gitea | Harbor | Headlamp |
|-------|------------|--------|---------|-------|--------|----------|
| **cluster-admin** | cluster-admin | role:admin | Admin | Site Admin | Admin | Full Access |
| **developer** | edit (dev NS) | role:developer | Editor | User | Developer | Namespace |
| **viewer** | view | role:readonly | Viewer | Read Only | Guest | Read Only |
| **guest** | - | - | Viewer | - | - | - |

### 6.2 Detailed Permission Matrix

| Group | Permission | K8s | Argo | Graf | Gitea | Harbor | Head |
|-------|------------|:---:|:----:|:----:|:-----:|:------:|:----:|
| **cluster-admin** | 시스템/클러스터 관리 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 생성 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 수정 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 조회 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **developer** | 시스템/클러스터 관리 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 생성 | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 수정 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 조회 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **viewer** | 시스템/클러스터 관리 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 생성 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 수정 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 조회 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### 6.2 Namespace-level Permissions (developer)

개발자는 특정 네임스페이스에 대해 추가 권한 부여 가능:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: dev-team-admin
  namespace: dev-team
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: admin
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developer
```

## 7. Token & Session Management

### 7.1 Token Lifetimes

| Token Type | Default Lifetime | Description |
|------------|------------------|-------------|
| Access Token | 5 minutes | API 요청 인증 |
| Refresh Token | 30 minutes | Access Token 갱신 |
| SSO Session | 10 hours | Keycloak 세션 |
| Offline Token | 30 days | 오프라인 접근 |

### 7.2 kubectl OIDC Login

```bash
# kubelogin (oidc-login plugin) 사용
kubectl oidc-login setup \
  --oidc-issuer-url=https://keycloak.local.narwhal.io/realms/kubernetes \
  --oidc-client-id=kubernetes

# kubeconfig 설정
kubectl config set-credentials oidc \
  --exec-api-version=client.authentication.k8s.io/v1beta1 \
  --exec-command=kubectl \
  --exec-arg=oidc-login \
  --exec-arg=get-token \
  --exec-arg=--oidc-issuer-url=https://keycloak.local.narwhal.io/realms/kubernetes \
  --exec-arg=--oidc-client-id=kubernetes \
  --exec-arg=--insecure-skip-tls-verify
```

**주의**: Self-signed 인증서 환경에서는 `--insecure-skip-tls-verify` 플래그 필요

## 8. Security Best Practices

### 8.1 Production Recommendations

1. **HTTPS 적용**: 모든 OIDC 통신에 TLS 사용 (K8s 1.35+ 필수)
2. **Secret 관리**: Client secrets를 Kubernetes Secrets 또는 OpenBao에 저장
3. **Token Lifetime 단축**: Access Token을 5분 이하로 설정
4. **MFA 활성화**: Keycloak에서 2FA 설정
5. **Audit Logging**: Keycloak 및 K8s audit log 활성화

### 8.2 Self-Signed Certificate Trust Chain

Narwhal은 개발 환경에서 자체 서명 인증서를 사용합니다.

**인증서 배포 구조:**

1. **cert-manager**: wildcard `*.local.narwhal.io` 자체 서명 CA 생성
2. **CA Secret 배포**: 각 앱 namespace에 `narwhal-ca-cert` Secret 복사
3. **Volume Mount**: CA cert를 `/etc/ssl/certs/narwhal-ca.crt`에 subPath로 마운트
4. **자동 인식**: Go 런타임이 `/etc/ssl/certs/` 디렉토리를 스캔하여 CA 자동 신뢰

**Per-App TLS Skip 설정 (대안):**

| App | 설정 방법 |
|-----|----------|
| ArgoCD | `argocd-cm` → `oidc.tls.insecure.skip.verify: "true"` |
| Grafana | `auth.generic_oauth.tls_skip_verify_insecure: true` |
| Harbor | `oidc_verify_cert: "false"` + `oidc_endpoint: https://...` |
| Headlamp | CA cert mount (v0.40.0에 `-oidc-skip-issuer-tls-verify` 플래그 없음) |
| OAuth2 Proxy | `ssl_insecure_skip_verify = true` |

**주의사항:**
- `SSL_CERT_FILE` env var 사용 금지 (시스템 CA 번들 전체가 대체됨)
- subPath mount는 디렉토리 내 다른 파일에 영향 없음
- 프로덕션 환경에서는 신뢰된 CA의 인증서 사용 권장

### 8.3 Principle of Least Privilege

```
cluster-admin: 플랫폼 관리자만 (최소 인원)
developer: 개발팀 (dev NS edit, devtools/monitoring view)
viewer: 운영 모니터링, 외부 사용자
guest: 웹 UI OIDC만 (K8s 접근 없음)
```

### 8.4 Group Management

새 사용자 추가 시:
1. Keycloak Admin Console 접속
2. Users → Add user
3. 사용자 생성 후 Groups 탭에서 적절한 그룹 할당
4. Credentials 탭에서 초기 비밀번호 설정

## 9. Troubleshooting

### 9.1 Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| API 서버 크래시 (1.35+) | HTTP issuer URL | HTTPS issuer URL로 변경, cert-manager TLS 확인 |
| OIDC login 실패 | Issuer URL 불일치 | API Server oidc-issuer-url 확인 |
| 권한 없음 | 그룹 미할당 | Keycloak에서 사용자 그룹 확인 |
| Token 만료 | Refresh 실패 | kubelogin 재인증 |
| Groups claim 없음 | Scope 미설정 | Realm-level `groups` client scope 확인 |
| `invalid_scope` 에러 | `groups` scope 미할당 | 모든 클라이언트에 default scope 할당 확인 |
| TLS 인증서 에러 | Self-signed CA | CA cert mount 또는 TLS skip 설정 |

### 9.2 Debugging Commands

```bash
# Keycloak OIDC discovery 확인 (HTTPS, self-signed)
curl -k https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration

# Keycloak 토큰 확인 (HTTPS)
curl -k -X POST \
  https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token \
  -d "grant_type=password" \
  -d "client_id=kubernetes" \
  -d "username=admin" \
  -d "password=admin"

# 클러스터 내부 통신 테스트 (HTTP, 서비스명)
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl -X POST http://keycloak-service.iam.svc.cluster.local:8080/realms/kubernetes/protocol/openid-connect/token \
  -d "grant_type=password" -d "client_id=kubernetes" -d "username=admin" -d "password=admin"

# JWT 디코딩 (groups claim 확인)
echo "<access_token>" | cut -d'.' -f2 | base64 -d | jq

# K8s RBAC 확인
kubectl auth can-i --list --as=oidc:admin --as-group=oidc:cluster-admin

# API Server OIDC 설정 확인
kubectl -n kube-system get pod kube-apiserver-* -o yaml | grep -A5 oidc
```

## 10. References

- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [Kubernetes OIDC Authentication](https://kubernetes.io/docs/reference/access-authn-authz/authentication/#openid-connect-tokens)
- [ArgoCD RBAC](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/)
- [Grafana OAuth](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/configure-authentication/generic-oauth/)
