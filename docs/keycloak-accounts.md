# Keycloak SSO 계정 및 설정 가이드

## 접속 정보

| 항목 | 값 |
|------|-----|
| URL | https://keycloak.local.narwhal.io |
| Admin Console | https://keycloak.local.narwhal.io/admin |
| Admin ID | `temp-admin` |
| Admin PW | `502fd9d2e7eb4ef09e9449b05ffabcde` |
| Realm | `kubernetes` |

## 사용자 계정

| 사용자 | 비밀번호 | 이메일 | 그룹 | 역할 |
|--------|----------|--------|------|------|
| `admin` | `admin` | admin@local | cluster-admin | cluster-admin |
| `dev` | `dev` | dev@local | developer | developer |
| `view` | `view` | view@local | viewer | viewer |
| `guest` | `guest` | guest@local | guest | guest (웹 UI OIDC만) |

## 그룹

| 그룹 | 설명 | K8s RBAC |
|------|------|---------|
| `cluster-admin` | 클러스터 관리자 (전체 권한) | ClusterRoleBinding → cluster-admin |
| `developer` | 개발자 (dev NS edit, devtools/monitoring view) | RoleBindings |
| `viewer` | 읽기 전용 (dev/devtools/monitoring view) | RoleBindings |
| `guest` | 웹 UI 전용 (K8s 접근 없음) | RBAC 없음 |

## 네임스페이스

| NS | 포함 컴포넌트 |
|----|-------------|
| `platform-system` | MetalLB, Traefik, cert-manager, CNPG operator, Kyverno |
| `iam` | Keycloak, OAuth2-Proxy |
| `devtools` | ArgoCD, Gitea, Harbor, Headlamp |
| `monitoring` | Prometheus, Grafana, Loki, Tempo, Promtail |
| `storage` | SeaweedFS, Velero, OpenBao |
| `database` | narwhal-db (PostgreSQL) |
| `istio-system` | Istio |
| `dev` | developer 워크로드 전용 |

## OIDC 클라이언트

| Client ID | Type | Secret | Redirect URI | 용도 |
|-----------|------|--------|--------------|------|
| `kubernetes` | public | - | `*` | K8s API OIDC |
| `argocd` | confidential | `argocd-secret` | `https://argocd.local.narwhal.io/auth/callback` | GitOps CD |
| `grafana` | confidential | `grafana-secret` | `https://grafana.local.narwhal.io/login/generic_oauth` | 모니터링 |
| `gitea` | confidential | `gitea-secret` | `https://gitea.local.narwhal.io/user/oauth2/keycloak/callback` | Git 서버 |
| `harbor` | confidential | `harbor-secret` | `https://harbor.local.narwhal.io/c/oidc/callback` | 컨테이너 레지스트리 |
| `headlamp` | confidential | `headlamp-secret` | `https://headlamp.local.narwhal.io/*` | K8s UI |
| `oauth2-proxy` | confidential | `oauth2-proxy-secret` | `https://oauth2.local.narwhal.io/oauth2/callback` | SSO Proxy |

## Client Scopes

모든 클라이언트에 `groups` scope가 default로 할당됨.

| Scope | Protocol | Mapper |
|-------|----------|--------|
| `groups` | openid-connect | `oidc-group-membership-mapper` (claim: `groups`) |

기본 제공 scopes: `openid`, `profile`, `email` + 커스텀 `groups`

## Realm Roles

| Role | 설명 |
|------|------|
| `cluster-admin` | 전체 관리 권한 |
| `developer` | 개발 권한 |
| `viewer` | 읽기 전용 |

## 앱별 권한 매트릭스

| App | cluster-admin | developer | viewer | guest |
|-----|:---:|:---:|:---:|:---:|
| K8s API | cluster-admin (전체) | dev NS edit + devtools/monitoring view | dev/devtools/monitoring view | 접근 불가 |
| ArgoCD | role:admin | role:developer (sync/get) | role:readonly | 접근 불가 |
| Grafana | Admin | Editor | Viewer | Viewer |
| Gitea | Site Admin | User | User (read-only) | 접근 불가 |
| Harbor | Admin | Developer | Guest | 접근 불가 |
| Headlamp | Full | dev NS edit | dev NS view | 접근 불가 |

## SSO TLS 설정

Self-signed 인증서(cert-manager) 사용으로 각 앱에 TLS skip-verify 설정 필요:

| 앱 | 설정 |
|----|------|
| ArgoCD | `argocd-cm` → `oidc.tls.insecure.skip.verify: "true"` |
| OAuth2-Proxy | `--ssl-insecure-skip-verify=true` |
| Grafana | `tls_skip_verify_insecure = true` |
| Headlamp | CA cert를 `/etc/ssl/certs/narwhal-ca.crt`에 subPath 마운트 |
| Harbor | `oidc_verify_cert: false` |
| Gitea | CA cert를 `extraVolumes` + `extraContainerVolumeMounts`로 마운트 |

## OIDC Endpoints

| Endpoint | URL |
|----------|-----|
| Issuer | `https://keycloak.local.narwhal.io/realms/kubernetes` |
| Authorization | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/auth` |
| Token | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token` |
| UserInfo | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/userinfo` |
| JWKS | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/certs` |
