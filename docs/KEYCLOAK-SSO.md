# Keycloak SSO & Authorization Guide

Narwhal IDP 플랫폼의 Keycloak SSO 연동 및 권한 관리 가이드

## 1. Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Keycloak (IAM/SSO)                         │
│                    Realm: kubernetes                            │
├─────────────────────────────────────────────────────────────────┤
│  Groups          │  Realm Roles      │  OIDC Clients            │
│  ───────────     │  ───────────      │  ───────────             │
│  cluster-admins  │  cluster-admin    │  kubernetes (K8s API)    │
│  developers      │  developer        │  argocd                  │
│  viewers         │  viewer           │  grafana                 │
│                  │                   │  gitea                   │
│                  │                   │  harbor                  │
│                  │                   │  headlamp                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Applications                                  │
│  K8s API │ ArgoCD │ Grafana │ Gitea │ Harbor │ Headlamp        │
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
| `cluster-admins` | 클러스터 전체 관리자 | cluster-admin |
| `developers` | 개발자 (네임스페이스 단위 권한) | developer |
| `viewers` | 읽기 전용 사용자 | viewer |

### 2.3 Realm Roles

| Role | Description |
|------|-------------|
| `cluster-admin` | 클러스터 전체 관리 권한 |
| `developer` | 리소스 생성/수정/삭제 권한 |
| `viewer` | 리소스 조회 권한만 |

### 2.4 Default Users

| Username | Password | Email | Group |
|----------|----------|-------|-------|
| `k8s-admin` | `k8s-admin` | k8s-admin@local | cluster-admins |
| `developer` | `developer` | developer@local | developers |

## 3. OIDC Clients

### 3.1 Client Configuration

| Client ID | Type | Secret | Redirect URIs |
|-----------|------|--------|---------------|
| `kubernetes` | Public | - | `*` |
| `argocd` | Confidential | `argocd-secret` | `https://argocd.local/*`, `http://localhost:8443/*` |
| `grafana` | Confidential | `grafana-secret` | `http://grafana.local/*`, `http://localhost:3000/*` |
| `gitea` | Confidential | `gitea-secret` | `http://gitea.local/*`, `http://localhost:3000/*` |
| `harbor` | Confidential | `harbor-secret` | `http://harbor.local/*`, `http://localhost:8080/*` |
| `headlamp` | Confidential | `headlamp-secret` | `http://headlamp.local/*`, `http://localhost:8080/*` |

### 3.2 Protocol Mappers

모든 클라이언트에 `groups` claim mapper 설정:

```json
{
  "name": "groups",
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

### 3.3 OIDC Endpoints

| Endpoint | URL |
|----------|-----|
| Issuer | `http://keycloak.keycloak.svc.cluster.local/realms/kubernetes` |
| Authorization | `{issuer}/protocol/openid-connect/auth` |
| Token | `{issuer}/protocol/openid-connect/token` |
| UserInfo | `{issuer}/protocol/openid-connect/userinfo` |
| JWKS | `{issuer}/protocol/openid-connect/certs` |
| Discovery | `{issuer}/.well-known/openid-configuration` |

## 4. Kubernetes OIDC Integration

### 4.1 API Server OIDC Configuration

`kubeadm-config.yaml`:
```yaml
apiServer:
  extraArgs:
    oidc-issuer-url: "http://keycloak.keycloak.svc.cluster.local/realms/kubernetes"
    oidc-client-id: "kubernetes"
    oidc-username-claim: "preferred_username"
    oidc-username-prefix: "oidc:"
    oidc-groups-claim: "groups"
    oidc-groups-prefix: "oidc:"
```

### 4.2 RBAC Mapping

| Keycloak Group | K8s Group (prefixed) | ClusterRole | Permissions |
|----------------|---------------------|-------------|-------------|
| `cluster-admins` | `oidc:cluster-admins` | `cluster-admin` | 전체 클러스터 관리 |
| `developers` | `oidc:developers` | `edit` | 리소스 CRUD (RBAC 제외) |
| `viewers` | `oidc:viewers` | `view` | 리소스 읽기 전용 |

### 4.3 ClusterRoleBindings

```yaml
# cluster-admins → cluster-admin
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-cluster-admins
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:cluster-admins

---
# developers → edit
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-developers
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: edit
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developers

---
# viewers → view
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-viewers
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:viewers
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
g, cluster-admins, role:admin
g, developers, role:developer
```

| Keycloak Group | ArgoCD Role | Permissions |
|----------------|-------------|-------------|
| `cluster-admins` | `role:admin` | 모든 앱/프로젝트 관리 |
| `developers` | `role:developer` | 앱 sync, 로그 조회 |
| (default) | `role:readonly` | 읽기 전용 |

**ArgoCD Built-in Roles:**

| Role | Permissions |
|------|-------------|
| `role:admin` | 모든 권한 (앱, 클러스터, 프로젝트, 설정) |
| `role:developer` | 앱 sync, 로그 조회, exec |
| `role:readonly` | 앱/리소스 조회만 |

### 5.2 Grafana

**Role Mapping (JMESPath):**
```
contains(groups[*], 'cluster-admins') && 'Admin' ||
contains(groups[*], 'developers') && 'Editor' ||
'Viewer'
```

| Keycloak Group | Grafana Role | Permissions |
|----------------|--------------|-------------|
| `cluster-admins` | `Admin` | 사용자/데이터소스/대시보드 관리 |
| `developers` | `Editor` | 대시보드 생성/수정 |
| `viewers` | `Viewer` | 대시보드 조회만 |

**Grafana Built-in Roles:**

| Role | Permissions |
|------|-------------|
| `Admin` | 조직 설정, 사용자 관리, 데이터소스 관리 |
| `Editor` | 대시보드/폴더 생성/수정, 알림 규칙 관리 |
| `Viewer` | 대시보드 조회, 탐색 |

### 5.3 Gitea

**OIDC Configuration:**
```
admin-group: cluster-admins
```

| Keycloak Group | Gitea Role | Permissions |
|----------------|------------|-------------|
| `cluster-admins` | Site Admin | 전체 관리자 |
| `developers` | User | 저장소 생성/관리 |
| `viewers` | User (제한적) | 공개 저장소 접근 |

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
  "oidc_admin_group": "cluster-admins",
  "oidc_groups_claim": "groups"
}
```

| Keycloak Group | Harbor Role | Permissions |
|----------------|-------------|-------------|
| `cluster-admins` | Admin | 전체 관리자 |
| `developers` | Developer | 이미지 Push/Pull |
| `viewers` | Guest | 이미지 Pull만 |

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
| `cluster-admins` | Full Access | cluster-admin |
| `developers` | Namespace Access | edit |
| `viewers` | Read Only | view |

Headlamp은 K8s RBAC을 직접 사용하므로, Keycloak 그룹 → K8s ClusterRole 매핑을 따릅니다.

## 6. Permission Matrix

### 6.1 Overall Permission Matrix

| Group | Kubernetes | ArgoCD | Grafana | Gitea | Harbor | Headlamp |
|-------|------------|--------|---------|-------|--------|----------|
| **cluster-admins** | cluster-admin | role:admin | Admin | Site Admin | Admin | Full Access |
| **developers** | edit | role:developer | Editor | User | Developer | Namespace |
| **viewers** | view | role:readonly | Viewer | Read Only | Guest | Read Only |

### 6.2 Detailed Permission Matrix

| Group | Permission | K8s | Argo | Graf | Gitea | Harbor | Head |
|-------|------------|:---:|:----:|:----:|:-----:|:------:|:----:|
| **cluster-admins** | 시스템/클러스터 관리 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 생성 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 수정 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 조회 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **developers** | 시스템/클러스터 관리 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 생성 | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 수정 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| | 리소스/앱 조회 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **viewers** | 시스템/클러스터 관리 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 생성 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 수정 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| | 리소스/앱 조회 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### 6.2 Namespace-level Permissions (developers)

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
  name: oidc:developers
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
  --oidc-issuer-url=http://keycloak.keycloak.svc.cluster.local/realms/kubernetes \
  --oidc-client-id=kubernetes

# kubeconfig 설정
kubectl config set-credentials oidc \
  --exec-api-version=client.authentication.k8s.io/v1beta1 \
  --exec-command=kubectl \
  --exec-arg=oidc-login \
  --exec-arg=get-token \
  --exec-arg=--oidc-issuer-url=http://keycloak.keycloak.svc.cluster.local/realms/kubernetes \
  --exec-arg=--oidc-client-id=kubernetes
```

## 8. Security Best Practices

### 8.1 Production Recommendations

1. **HTTPS 적용**: 모든 OIDC 통신에 TLS 사용
2. **Secret 관리**: Client secrets를 Kubernetes Secrets 또는 OpenBao에 저장
3. **Token Lifetime 단축**: Access Token을 5분 이하로 설정
4. **MFA 활성화**: Keycloak에서 2FA 설정
5. **Audit Logging**: Keycloak 및 K8s audit log 활성화

### 8.2 Principle of Least Privilege

```
cluster-admins: 플랫폼 관리자만 (최소 인원)
developers: 개발팀 (필요한 네임스페이스만)
viewers: 운영 모니터링, 외부 사용자
```

### 8.3 Group Management

새 사용자 추가 시:
1. Keycloak Admin Console 접속
2. Users → Add user
3. 사용자 생성 후 Groups 탭에서 적절한 그룹 할당
4. Credentials 탭에서 초기 비밀번호 설정

## 9. Troubleshooting

### 9.1 Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| OIDC login 실패 | Issuer URL 불일치 | API Server oidc-issuer-url 확인 |
| 권한 없음 | 그룹 미할당 | Keycloak에서 사용자 그룹 확인 |
| Token 만료 | Refresh 실패 | kubelogin 재인증 |
| Groups claim 없음 | Mapper 미설정 | Client protocol mapper 확인 |

### 9.2 Debugging Commands

```bash
# Keycloak 토큰 확인
curl -X POST \
  http://keycloak.keycloak.svc.cluster.local/realms/kubernetes/protocol/openid-connect/token \
  -d "grant_type=password" \
  -d "client_id=kubernetes" \
  -d "username=k8s-admin" \
  -d "password=k8s-admin"

# JWT 디코딩 (groups claim 확인)
echo "<access_token>" | cut -d'.' -f2 | base64 -d | jq

# K8s RBAC 확인
kubectl auth can-i --list --as=oidc:k8s-admin --as-group=oidc:cluster-admins
```

## 10. References

- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [Kubernetes OIDC Authentication](https://kubernetes.io/docs/reference/access-authn-authz/authentication/#openid-connect-tokens)
- [ArgoCD RBAC](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/)
- [Grafana OAuth](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/configure-authentication/generic-oauth/)
