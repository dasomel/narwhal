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
| `k8s-admin` | `k8s-admin` | k8s-admin@local | cluster-admins | cluster-admin |
| `developer` | `developer` | developer@local | developers | developer |

## 그룹

| 그룹 | 설명 |
|------|------|
| `cluster-admins` | 클러스터 관리자 (전체 권한) |
| `developers` | 개발자 (네임스페이스 제한) |
| `viewers` | 읽기 전용 |

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

**Headlamp 참고**: v0.40.0에 `-oidc-skip-issuer-tls-verify` 플래그가 없으므로, `narwhal-ca-cert` Secret에서 CA cert를 `/etc/ssl/certs/narwhal-ca.crt`에 subPath로 마운트. Go 런타임이 `/etc/ssl/certs/` 디렉토리를 자동 스캔.

**Gitea 참고**: Gitea Go 런타임이 Keycloak HTTPS 엔드포인트를 검증하므로, Helm values에서 `extraVolumes` + `extraContainerVolumeMounts`로 CA cert 마운트 필요.

## OIDC Endpoints

| Endpoint | URL |
|----------|-----|
| Issuer | `https://keycloak.local.narwhal.io/realms/kubernetes` |
| Authorization | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/auth` |
| Token | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token` |
| UserInfo | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/userinfo` |
| JWKS | `https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/certs` |
