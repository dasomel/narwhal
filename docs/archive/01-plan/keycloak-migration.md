# Keycloak 마이그레이션 계획

> Authentik → Keycloak 전환 및 OSS별 SSO 등록

**작성일**: 2026-04-05  
**상태**: 계획

---

## 1. 배경 및 목표

### 현황
- IAM: Authentik `2026.2.1` (namespace: `iam`)
- SSO 방식: 단일 `apisix` confidential client → APISIX openid-connect plugin이 모든 서비스 인증 처리
- K8s OIDC: `kubernetes` public provider (kubectl-oidc-login)
- 서비스 자체 native OIDC/OAuth 미구성 (APISIX gateway-level 인증만)

### 목표
1. Keycloak Operator 기반 Keycloak 설치
2. 모든 OSS 서비스에 **서비스별 전용 Keycloak client** 등록 (Keycloak에서 앱별 관리)
3. **SSO는 APISIX openid-connect plugin이 전담** — 서비스별 native OIDC 설정 없음
4. K8s API Server OIDC를 Keycloak으로 전환

### 설계 원칙
- Keycloak Operator 사용 (Helm chart 아님) — Operator가 Ingress 자동 관리
- **HTTPRoute 생성 금지** — Operator Ingress와 충돌 시 502 발생
- 모든 OIDC client에 **audience mapper 필수** 추가
- `emailVerified: true` 모든 사용자
- `groups` scope: realm-level 커스텀 scope 생성, `microprofile-jwt` 중복 mapper 삭제

---

## 2. 스크립트/파일 매핑

| 현재 (Authentik) | 신규 (Keycloak) | 설명 |
|---|---|---|
| `scripts/cluster/11-authentik.sh` | `scripts/cluster/11-keycloak.sh` | Keycloak Operator + CR 설치 |
| `scripts/cluster/11-2-authentik-config.sh` | `scripts/cluster/11-2-keycloak-config.sh` | Realm, Users, Groups, apisix/kubernetes clients |
| *(없음)* | `scripts/cluster/11-3-keycloak-clients.sh` | OSS별 서비스 clients 등록 |
| `scripts/cluster/11-4-authentik-apiserver.sh` | `scripts/cluster/11-4-keycloak-apiserver.sh` | K8s API Server OIDC 설정 |
| `gitops/apps/authentik.yaml` | `gitops/apps/keycloak.yaml` | GitOps ArgoCD Application |
| `gitops/resources/apisix-routes.yaml` | `gitops/resources/apisix-routes.yaml` | discovery URL 변경 |
| `docs/authentik-sso.md` | `docs/keycloak-sso.md` | 운영 문서 |

---

## 3. 아키텍처 설계

### Realm 구조
```
Realm: narwhal
├── Groups
│   ├── cluster-admin
│   ├── developer
│   ├── viewer
│   └── guest
├── Users
│   ├── admin        → cluster-admin 그룹
│   ├── dev          → developer 그룹
│   ├── view         → viewer 그룹
│   └── guest        → guest 그룹
├── Custom Scope: groups (realm-level)
│   └── Mapper: groups (Group Membership, full path: false)
└── Clients
    ├── kubernetes   (public)          — kubectl-oidc-login 전용
    ├── argocd       (confidential)    — APISIX route용 (서비스별 Keycloak 앱 분리)
    ├── grafana      (confidential)    — APISIX route용
    ├── gitea        (confidential)    — APISIX route용
    ├── harbor       (confidential)    — APISIX route용
    ├── headlamp     (confidential)    — APISIX route용
    ├── velero-ui    (confidential)    — APISIX route용
    ├── hubble       (confidential)    — APISIX route용
    ├── prometheus   (confidential)    — APISIX route용
    └── openbao      (confidential)    — APISIX route용
```

> **핵심**: 모든 confidential client는 APISIX openid-connect plugin이 사용.  
> 서비스 자체에는 OIDC 설정 없음. Keycloak에서 서비스별 앱으로 분리하여 감사/정책 적용.

### Issuer URL
```
https://keycloak.local.narwhal.internal/realms/narwhal
```

### SSO 흐름
```
브라우저 → APISIX Gateway
  → 라우트별 openid-connect plugin (서비스 전용 client_id 사용)
  → 미인증 → Keycloak 로그인 (서비스별 client로 리다이렉트)
  → /apisix/callback → 세션 생성
  → upstream service로 X-Userinfo / Authorization: Bearer 헤더 전달
  (서비스 자체는 OIDC 처리 안 함 — APISIX가 전담)

kubectl → kubectl-oidc-login → Keycloak (kubernetes client, public)
  → ID Token groups 클레임 → K8s API Server OIDC → RBAC
```

---

## 4. Phase별 작업 계획

### Phase 1: Keycloak 설치 (`11-keycloak.sh`)

**목표**: Keycloak Operator + Keycloak CR 배포, APISIX ExternalName 서비스 생성

```
1-1. Namespace 생성: iam
1-2. CNPG DB 사용자/DB 생성 (narwhal-db 클러스터 재사용)
     - user: keycloak, db: keycloak
     - Secret: keycloak-db-secret (iam ns)
1-3. Keycloak Operator CRD + RBAC 설치
     - 출처: https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/refs/tags/26.x.x/kubernetes/kubernetes.yml
     - namespace: iam
1-4. Keycloak CR 생성
     - hostname.hostname: keycloak.local.narwhal.internal
     - hostname.strict: false (내부 서비스에서 IP로 접근 허용)
     - proxy.headers: xforwarded
     - db: keycloak-db-secret 참조
     - instances: 1 (dev 환경)
     - TLS: ingress TLS termination (cert-manager wildcard cert)
1-5. Operator가 관리하는 Ingress 확인 대기
     - Keycloak Operator가 자동으로 Ingress 생성 (HTTPRoute 생성 금지)
1-6. APISIX ExternalName Service 생성 (platform-system ns)
     - keycloak.iam.svc.cluster.local 참조
1-7. APISIX route 추가 (keycloak.local.narwhal.internal 노출)
```

**주의사항**:
- Keycloak Operator는 `keycloak-network-policy` NetworkPolicy를 자동 생성 — 직접 수정 금지
- Istio ambient 환경에서 Keycloak pod에 `istio.io/dataplane-mode: none` 레이블 필요
  → `spec.unsupported.podTemplate.metadata.labels`에 설정
- `--oidc-ca-file` 용 TLS 인증서 추출 준비

---

### Phase 2: Realm 및 기본 Clients 설정 (`11-2-keycloak-config.sh`)

**목표**: Realm, 사용자/그룹, 공통 clients (apisix, kubernetes) 구성

**사용 도구**: `kcadm.sh` (Keycloak Admin CLI)

```
2-1. Admin CLI 초기화
     - kcadm.sh config credentials --server http://localhost:8080 \
         --realm master --user admin --password <bootstrap-password>

2-2. Realm 생성: narwhal
     - displayName: "Narwhal IDP"
     - enabled: true
     - sslRequired: external (HTTPS 강제)
     - registrationAllowed: false

2-3. 커스텀 groups scope 생성 (realm-level)
     - name: groups, protocol: openid-connect
     - Mapper: Group Membership, claim name: groups, full path: false
     - microprofile-jwt scope의 groups mapper 삭제 (중복 방지)

2-4. Groups 생성
     - cluster-admin, developer, viewer, guest

2-5. Users 생성 (emailVerified: true 필수)
     - admin → cluster-admin, dev → developer, view → viewer, guest → guest
     - 패스워드는 keycloak-user-passwords secret에서 읽음

2-6. kubernetes client (public)
     - clientId: kubernetes, publicClient: true
     - redirectUris: http://localhost:*/callback (kubectl-oidc-login)
     - scope: openid, profile, email, groups
     - Mappers:
       - username → preferred_username (User Property)
       - groups → groups (Group Membership)
       - Audience Mapper: included.client.audience=kubernetes

2-7. apisix client (confidential)
     - clientId: apisix, secret: 자동 생성
     - redirectUris: https://*.local.narwhal.internal/apisix/callback
     - scope: openid, profile, email, groups
     - Mappers: username, groups, Audience Mapper (apisix)
     - client secret → apisix-oidc-config secret (platform-system ns)

2-8. K8s secrets 생성
     - apisix-oidc-config (platform-system): client_secret, session_secret
     - keycloak-oidc-base (iam): issuer_url, realm_url (공통 참조용)
```

---

### Phase 3: OSS별 Keycloak Client 등록 + APISIX Route 설정 (`11-3-keycloak-clients.sh`)

**목표**: 각 서비스 전용 Keycloak client 생성 → K8s Secret 저장 → APISIX route에 openid-connect plugin 설정

**SSO 방식**: 모든 인증은 APISIX openid-connect plugin이 처리. 서비스 자체 OIDC 설정 없음.

**공통 client 설정**:
```
- client_type: confidential
- scope: openid, profile, email, groups
- Mappers (모든 client 공통):
    1. username → preferred_username (User Property Mapper)
    2. groups → groups (Group Membership Mapper, full path: false)
    3. Audience Mapper: included.client.audience=<client-id>  ← 필수
- redirectUris: https://<service>.local.narwhal.internal/apisix/callback
- webOrigins: https://<service>.local.narwhal.internal
```

**APISIX openid-connect plugin 공통 구조**:
```yaml
plugins:
  - name: openid-connect
    enable: true
    config:
      client_id: "<service-client-id>"
      client_secret: "$secret://kubernetes/k8s-1/<service>-oidc-secret/client_secret"
      discovery: "https://keycloak.local.narwhal.internal/realms/narwhal/.well-known/openid-configuration"
      redirect_uri: "https://<service>.local.narwhal.internal/apisix/callback"
      scope: "openid email profile groups"
      bearer_only: false
      ssl_verify: false
      logout_path: "/apisix/logout"
      set_userinfo_header: true
      set_access_token_header: true
      access_token_in_authorization_header: true
      session:
        secret: "$secret://kubernetes/k8s-1/<service>-oidc-secret/session_secret"
```

---

#### 3-1. ArgoCD
```
Keycloak Client:
  clientId: argocd
  redirectUri: https://argocd.local.narwhal.internal/apisix/callback
  Secret → argocd-oidc-secret (platform-system ns)

APISIX Route: argocd.local.narwhal.internal
  upstream: argocd-server.devtools:80
  plugin: openid-connect (client_id=argocd)
```

#### 3-2. Grafana
```
Keycloak Client:
  clientId: grafana
  redirectUri: https://grafana.local.narwhal.internal/apisix/callback
  Secret → grafana-oidc-secret (platform-system ns)

APISIX Route: grafana.local.narwhal.internal
  upstream: prometheus-stack-grafana.monitoring:80
  plugin: openid-connect (client_id=grafana)
```

#### 3-3. Gitea
```
Keycloak Client:
  clientId: gitea
  redirectUri: https://gitea.local.narwhal.internal/apisix/callback
  Secret → gitea-oidc-secret (platform-system ns)

APISIX Route: gitea.local.narwhal.internal
  upstream: gitea-http.devtools:3000
  plugin: openid-connect (client_id=gitea)
```

#### 3-4. Harbor
```
Keycloak Client:
  clientId: harbor
  redirectUri: https://harbor.local.narwhal.internal/apisix/callback
  Secret → harbor-oidc-secret (platform-system ns)

APISIX Route: harbor.local.narwhal.internal
  upstream: harbor-nginx.devtools:80
  plugin: openid-connect (client_id=harbor)
         + client-control (body_size: 104857600)  ← 이미지 push용
```

#### 3-5. Headlamp
```
Keycloak Client:
  clientId: headlamp
  redirectUri: https://headlamp.local.narwhal.internal/apisix/callback
  Secret → headlamp-oidc-secret (platform-system ns)

APISIX Route: headlamp.local.narwhal.internal
  upstream: headlamp.devtools:80
  plugin: openid-connect (client_id=headlamp)
```

#### 3-6. Velero UI
```
Keycloak Client:
  clientId: velero-ui
  redirectUri: https://velero-ui.local.narwhal.internal/apisix/callback
  Secret → velero-ui-oidc-secret (platform-system ns)

APISIX Route: velero-ui.local.narwhal.internal
  upstream: velero-ui.storage:3000
  plugin: openid-connect (client_id=velero-ui)
```

#### 3-7. Hubble UI
```
Keycloak Client:
  clientId: hubble
  redirectUri: https://hubble.local.narwhal.internal/apisix/callback
  Secret → hubble-oidc-secret (platform-system ns)

APISIX Route: hubble.local.narwhal.internal
  upstream: hubble-ui.kube-system:80
  plugin: openid-connect (client_id=hubble)
```

#### 3-8. Prometheus / Alertmanager
```
Keycloak Client:
  clientId: prometheus
  redirectUri: https://prometheus.local.narwhal.internal/apisix/callback,
               https://alertmanager.local.narwhal.internal/apisix/callback
  Secret → prometheus-oidc-secret (platform-system ns)

APISIX Routes:
  prometheus.local.narwhal.internal   → prometheus-server:9090
  alertmanager.local.narwhal.internal → alertmanager-server:9093
  (동일 client 공유 가능, redirectUri 2개 등록)
```

#### 3-9. OpenBao
```
Keycloak Client:
  clientId: openbao
  redirectUri: https://openbao.local.narwhal.internal/apisix/callback
  Secret → openbao-oidc-secret (platform-system ns)

APISIX Route: openbao.local.narwhal.internal
  upstream: openbao.storage:8200
  plugin: openid-connect (client_id=openbao)
```

---

**Phase 3 완료 후**: 모든 서비스 K8s Secret을 `platform-system` ns에 생성하여 APISIX Secret Provider가 참조 가능하도록 함.

---

### Phase 4: K8s API Server OIDC 전환 (`11-4-keycloak-apiserver.sh`)

**목표**: kube-apiserver OIDC 설정을 Keycloak issuer로 변경

```
4-1. Keycloak TLS 인증서 추출 (self-signed CA)
     openssl s_client -connect keycloak.local.narwhal.internal:443 -showcerts \
       2>/dev/null | openssl x509 -outform PEM > /tmp/keycloak-ca.crt

4-2. kube-apiserver static pod manifest 수정 (yq 사용)
     /etc/kubernetes/manifests/kube-apiserver.yaml
     --oidc-issuer-url=https://keycloak.local.narwhal.internal/realms/narwhal
     --oidc-client-id=kubernetes
     --oidc-username-claim=preferred_username
     --oidc-username-prefix=oidc:
     --oidc-groups-claim=groups
     --oidc-groups-prefix=oidc:
     --oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt

4-3. master-2, master-3에 SSH로 동일 변경 전파

4-4. API server 재시작 대기 및 헬스체크

4-5. ClusterRoleBinding 생성 (그룹 prefix 유지: oidc:)
     - cluster-admin → oidc:cluster-admin
     - edit (dev ns) → oidc:developer
     - view (devtools/monitoring) → oidc:viewer

4-6. 검증
     kubectl --token=<oidc-token> get nodes
```

---

### Phase 5: APISIX Routes 업데이트

**목표**: discovery URL을 Authentik → Keycloak으로 변경

```yaml
# 변경 전
discovery: "https://authentik.local.narwhal.internal/application/o/apisix/.well-known/openid-configuration"

# 변경 후
discovery: "https://keycloak.local.narwhal.internal/realms/narwhal/.well-known/openid-configuration"
```

- `redirect_uri` 패턴 유지 (`/apisix/callback`)
- `client_id: apisix`, `client_secret` Secret 참조 업데이트
- Authentik route 항목 제거 (또는 Keycloak route로 교체)

---

### Phase 6: GitOps 업데이트

```
6-1. gitops/apps/authentik.yaml → gitops/apps/keycloak.yaml 교체
     - Keycloak Operator는 CRD+RBAC를 스크립트로 설치
     - GitOps는 Keycloak CR만 관리 (또는 스크립트 완전 관리)

6-2. app-of-apps.yaml 업데이트
     - authentik → keycloak

6-3. Gitea 레포 push (ArgoCD selfHeal 고려)
     - GITEA_POD_IP 방식으로 push

6-4. ArgoCD sync 확인
```

---

## 5. 서비스별 SSO 구성 요약표

> SSO 방식: **전 서비스 APISIX openid-connect plugin** (서비스 자체 OIDC 설정 없음)

| 서비스 | Keycloak Client ID | Secret 위치 | APISIX Upstream | Callback URL |
|---|---|---|---|---|
| kubectl | kubernetes (public) | — | — | localhost:*/callback |
| ArgoCD | argocd | platform-system/argocd-oidc-secret | argocd-server.devtools:80 | argocd.local.narwhal.internal/apisix/callback |
| Grafana | grafana | platform-system/grafana-oidc-secret | prometheus-stack-grafana.monitoring:80 | grafana.local.narwhal.internal/apisix/callback |
| Gitea | gitea | platform-system/gitea-oidc-secret | gitea-http.devtools:3000 | gitea.local.narwhal.internal/apisix/callback |
| Harbor | harbor | platform-system/harbor-oidc-secret | harbor-nginx.devtools:80 | harbor.local.narwhal.internal/apisix/callback |
| Headlamp | headlamp | platform-system/headlamp-oidc-secret | headlamp.devtools:80 | headlamp.local.narwhal.internal/apisix/callback |
| Velero UI | velero-ui | platform-system/velero-ui-oidc-secret | velero-ui.storage:3000 | velero-ui.local.narwhal.internal/apisix/callback |
| Hubble UI | hubble | platform-system/hubble-oidc-secret | hubble-ui.kube-system:80 | hubble.local.narwhal.internal/apisix/callback |
| Prometheus | prometheus | platform-system/prometheus-oidc-secret | prometheus-server:9090 | prometheus.local.narwhal.internal/apisix/callback |
| Alertmanager | prometheus (공유) | platform-system/prometheus-oidc-secret | alertmanager-server:9093 | alertmanager.local.narwhal.internal/apisix/callback |
| OpenBao | openbao | platform-system/openbao-oidc-secret | openbao.storage:8200 | openbao.local.narwhal.internal/apisix/callback |

---

## 6. CLAUDE.md 과거 실수에서 배운 주의사항

| 항목 | 주의사항 |
|---|---|
| Keycloak Operator Ingress | Operator가 자동으로 Ingress 생성 → **HTTPRoute 생성 금지** (502 발생) |
| hostname 설정 | v2 형식: `hostname.hostname` + `hostname.strict` + `proxy.headers: xforwarded` (v1 `hostname-url` 사용 금지) |
| Audience Mapper | **모든 OIDC client**에 `oidc-audience-mapper` 추가 필수 (없으면 `expected audience "X" got ["account"]`) |
| groups scope | realm-level 커스텀 scope 생성 후 `microprofile-jwt` scope의 groups mapper 삭제 (중복 시 오류) |
| emailVerified | 모든 사용자 생성 시 `-s emailVerified=true` 필수 (없으면 OAuth2-Proxy 500) |
| kcadm.sh ID 조회 | CSV format 불안정 → `kcadm.sh get ... \| jq -r '.[] \| select(.name=="X") \| .id'` 사용 |
| `--oidc-ca-file` | self-signed cert 환경에서 `--oidc-ca-file` 없으면 JWKS verify 실패 |
| Gitea OAuth2 source name | `--name "keycloak"` 소문자 필수 (`/user/oauth2/keycloak` URL 경로 대소문자 구분) |
| Istio ambient + Keycloak | Keycloak pod에 `istio.io/dataplane-mode: none` 레이블 필요 (Set-Cookie 손상 방지) |
| NetworkPolicy 15008 | Operator managed `keycloak-network-policy`는 직접 수정 금지 → 별도 `keycloak-allow-hbone` NetworkPolicy 생성 |
| Keycloak Operator RBAC namespace | Subject namespace가 실제 설치 namespace와 다를 경우 ClusterRoleBinding patch 필요 |

---

## 7. 검증 체크리스트

### Keycloak 설치 검증
- [ ] Keycloak pod Running
- [ ] `https://keycloak.local.narwhal.internal` 접속 가능
- [ ] Admin Console 로그인 성공
- [ ] `.well-known/openid-configuration` 엔드포인트 응답

### SSO 검증 (APISIX openid-connect 공통 흐름)
- [ ] ArgoCD: 미인증 접근 → Keycloak(argocd client) 리다이렉트 → 로그인 → 서비스 진입
- [ ] Grafana: 미인증 접근 → Keycloak(grafana client) 리다이렉트 → 로그인 → 서비스 진입
- [ ] Gitea: 미인증 접근 → Keycloak(gitea client) 리다이렉트 → 로그인 → 서비스 진입
- [ ] Harbor: 미인증 접근 → Keycloak(harbor client) 리다이렉트 → 로그인 → 이미지 push 가능
- [ ] Headlamp: 미인증 접근 → Keycloak(headlamp client) 리다이렉트 → 로그인 → K8s 리소스 확인
- [ ] Velero UI: 미인증 접근 → Keycloak(velero-ui client) 리다이렉트 → 로그인
- [ ] Hubble UI: 미인증 접근 → Keycloak(hubble client) 리다이렉트 → 로그인
- [ ] Prometheus/Alertmanager: 미인증 접근 → Keycloak(prometheus client) 리다이렉트 → 로그인
- [ ] OpenBao: 미인증 접근 → Keycloak(openbao client) 리다이렉트 → 로그인
- [ ] `/apisix/logout` → Keycloak 세션 종료 → 재접근 시 로그인 페이지

### K8s OIDC 검증
- [ ] `kubectl-oidc-login` → Keycloak 인증 → `kubectl get nodes` 성공
- [ ] `oidc:cluster-admin` 그룹 → ClusterAdmin 권한 확인
- [ ] `oidc:developer` 그룹 → dev namespace edit 권한 확인

### APISIX Gateway 검증
- [ ] 미인증 브라우저 → Keycloak 리다이렉트
- [ ] 로그인 후 원래 서비스 접근
- [ ] `/apisix/logout` 동작 확인

---

## 8. 롤백 계획

1. Authentik 스크립트는 별도 브랜치에 보존
2. CNPG DB는 별도 database (`authentik` vs `keycloak`) 사용 → 공존 가능
3. ArgoCD에서 `keycloak.yaml` → `authentik.yaml`로 교체 후 sync
4. K8s API Server OIDC 롤백: yq로 이전 issuer URL 복원

---

## 9. 구현 순서 (의존성)

```
11-keycloak.sh
    ↓
11-2-keycloak-config.sh  (Realm, Groups, Users, apisix/kubernetes clients)
    ↓
11-3-keycloak-clients.sh  (ArgoCD, Grafana, Gitea, Harbor, Headlamp, Velero UI, OpenBao clients)
    ↓ (병렬 가능)
11-4-keycloak-apiserver.sh    apisix-routes.yaml 업데이트
    ↓
14-gitops-bootstrap.sh (GitOps push)
```

---

## 10. 버전 정보

| 컴포넌트 | 버전 |
|---|---|
| Keycloak | 26.1.x (ARM64 지원) |
| Keycloak Operator | keycloak-k8s-resources 26.1.x |
| keycloak-js / kcadm | Keycloak 버전과 동일 |
| Realm | narwhal |
