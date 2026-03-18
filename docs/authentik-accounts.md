# Authentik SSO 계정 및 권한 관리 가이드

> **참고:** Keycloak → Authentik 마이그레이션 완료 (2026-03-17).
> 파일명은 하위 호환을 위해 유지. 모든 내용은 Authentik 기준입니다.

---

## 접속 정보

| 항목 | 값 |
|------|-----|
| URL | https://authentik.local.narwhal.io |
| Admin UI | https://authentik.local.narwhal.io/if/admin/ |
| Admin Email | `admin@local.narwhal.io` |
| Admin PW | 프로비저닝 시 자동생성 (아래 조회 명령어 참고) |

### Admin 비밀번호 조회

```bash
kubectl get secret authentik-bootstrap-secret -n iam \
  -o jsonpath='{.data.bootstrap_password}' | base64 -d && echo
```

### 사용자 비밀번호 조회

```bash
# 특정 사용자 비밀번호
kubectl get secret authentik-user-passwords -n iam \
  -o jsonpath='{.data.admin}' | base64 -d && echo

# 전체 조회
kubectl get secret authentik-user-passwords -n iam -o yaml
```

---

## 역할 체계

총 6개 그룹으로 **책임 분리(SoD)** 원칙을 적용합니다.

```
cluster-admin          전체 클러스터 최고 관리자
├── infra-admin        인프라 레이어 관리 (네트워크/스토리지/보안)
├── platform-admin     플랫폼 레이어 관리 (DevTools/GitOps)
├── developer          애플리케이션 개발자
├── viewer             읽기 전용 관찰자
└── guest              웹 UI 게스트 (K8s 접근 없음)
```

---

## 사용자 계정

| 사용자 | 그룹 | 이메일 | 비밀번호 |
|--------|------|--------|----------|
| `admin` | `cluster-admin` | admin@local.narwhal.io | 자동생성 (Secret 조회) |
| `infra` | `infra-admin` | infra@local.narwhal.io | 자동생성 |
| `platform` | `platform-admin` | platform@local.narwhal.io | 자동생성 |
| `dev` | `developer` | dev@local.narwhal.io | 자동생성 |
| `view` | `viewer` | view@local.narwhal.io | 자동생성 |
| `guest` | `guest` | guest@local.narwhal.io | 자동생성 |

> 모든 비밀번호는 `11-2-authentik-config.sh` 실행 시 `generate_password()`로 생성되어
> `authentik-user-passwords` Secret(iam ns)에 저장됩니다.

---

## 그룹별 권한 상세

### 1. cluster-admin — 클러스터 관리자

> 전체 클러스터 완전 권한. 비상시 모든 레이어 개입 가능.

| 영역 | 권한 |
|------|------|
| K8s | ClusterRoleBinding → `cluster-admin` (전체 네임스페이스) |
| ArgoCD | `role:admin` (앱 삭제/생성/설정 변경 포함) |
| Grafana | `Admin` (데이터소스/대시보드/사용자 관리) |
| Harbor | `Admin` (레지스트리 전체 관리) |
| Gitea | `Site Admin` |
| Headlamp | 전체 클러스터 접근 |
| OpenBao | 루트 토큰 접근 |

---

### 2. infra-admin — 인프라 관리

> 네트워크·스토리지·보안·모니터링 레이어 담당. 애플리케이션 배포 권한 없음.

| 영역 | 권한 |
|------|------|
| K8s | ClusterRole → 아래 네임스페이스 admin |
| `platform-system` | MetalLB, APISIX, cert-manager, Cilium, CNPG operator, Kyverno |
| `storage` | SeaweedFS, Velero, OpenBao |
| `database` | narwhal-db PostgreSQL 클러스터 |
| `monitoring` | Prometheus, Grafana, Loki, Tempo |
| `istio-system` | Istio ambient mesh |
| K8s (그외 NS) | `view` |
| ArgoCD | `role:readonly` + `infra` 카테고리 앱 sync 허용 |
| Grafana | `Admin` |
| Harbor | `Guest` (이미지 pull만) |
| Gitea | `User` |
| OpenBao | path-level 정책 관리 |

---

### 3. platform-admin — 플랫폼 관리

> DevTools·GitOps 레이어 담당. CI/CD 파이프라인, 이미지 레지스트리, 코드 저장소 관리.

| 영역 | 권한 |
|------|------|
| K8s | 아래 네임스페이스 admin |
| `devtools` | ArgoCD, Gitea, Harbor, Headlamp |
| `dev` | 개발 워크로드 네임스페이스 |
| K8s (그외 NS) | `view` |
| ArgoCD | `role:developer` (앱 sync/get, 설정 변경 불가) |
| Grafana | `Editor` |
| Harbor | `Project Admin` (이미지 push/pull/태그 관리) |
| Gitea | `Admin` (조직/레포 관리) |
| Headlamp | `devtools` + `dev` NS 전체 접근 |

---

### 4. developer — 개발자

> 개발 네임스페이스 워크로드 배포. 플랫폼 설정 변경 불가.

| 영역 | 권한 |
|------|------|
| K8s | `dev` NS: edit / `devtools`, `monitoring`: view |
| ArgoCD | `role:readonly` (앱 상태 조회만) |
| Grafana | `Editor` (대시보드 생성/편집) |
| Harbor | `Developer` (프로젝트 이미지 push/pull) |
| Gitea | `User` (레포 clone/push) |
| Headlamp | `dev` NS view |

---

### 5. viewer — 읽기 전용

> 전체 클러스터 관찰. 변경 권한 없음.

| 영역 | 권한 |
|------|------|
| K8s | ClusterRole → `view` (전체 NS) |
| ArgoCD | `role:readonly` |
| Grafana | `Viewer` |
| Harbor | `Guest` (public 프로젝트 pull만) |
| Gitea | `User` (public 레포 읽기) |
| Headlamp | ClusterRole view |

---

### 6. guest — 게스트

> Web UI 접근만 허용. K8s 직접 접근 불가. APISIX OIDC에서 차단됨.

| 영역 | 권한 |
|------|------|
| K8s | RBAC 없음 (토큰 발급 불가) |
| Grafana | `Viewer` (read-only 대시보드) |
| ArgoCD | 접근 차단 (APISIX allowed_groups에 미포함) |
| Harbor | 접근 차단 |
| Gitea | 접근 차단 |

---

## 앱별 권한 매트릭스

| App | cluster-admin | infra-admin | platform-admin | developer | viewer | guest |
|-----|:---:|:---:|:---:|:---:|:---:|:---:|
| K8s API | cluster-admin | 지정 NS admin | devtools/dev admin | dev edit | all view | ✗ |
| ArgoCD | Admin | Readonly+sync | Developer+sync | Readonly | Readonly | ✗ |
| Grafana | Admin | Admin | Editor | Editor | Viewer | Viewer |
| Gitea | Site Admin | User | Admin | User | User | ✗ |
| Harbor | Admin | Guest | Project Admin | Developer | Guest | ✗ |
| Headlamp | Full | All view | devtools+dev | dev view | All view | ✗ |
| OpenBao | Root | Policy Admin | ✗ | ✗ | ✗ | ✗ |

---

## 네임스페이스 접근 매트릭스

| Namespace | 컴포넌트 | cluster-admin | infra-admin | platform-admin | developer | viewer |
|-----------|---------|:---:|:---:|:---:|:---:|:---:|
| `platform-system` | MetalLB, APISIX, cert-manager, Kyverno, CNPG op | admin | admin | view | view | view |
| `iam` | Authentik, Valkey | admin | admin | view | view | view |
| `devtools` | ArgoCD, Gitea, Harbor, Headlamp | admin | view | admin | view | view |
| `monitoring` | Prometheus, Grafana, Loki, Tempo | admin | admin | view | view | view |
| `storage` | SeaweedFS, Velero, OpenBao | admin | admin | view | view | view |
| `database` | narwhal-db PostgreSQL | admin | admin | view | view | view |
| `istio-system` | Istio ambient | admin | admin | view | view | view |
| `dev` | 개발 워크로드 | admin | view | admin | edit | view |

---

## OIDC 구성 (Authentik)

### Provider 구조

| Provider | Type | Client ID | 용도 |
|----------|------|-----------|------|
| `kubernetes` | public | `kubernetes` | K8s API server OIDC |
| `apisix` | confidential | `apisix` | APISIX gateway 보호 앱 전체 |

> Keycloak의 앱별 클라이언트 방식 → Authentik은 단일 `apisix` provider로 통합.
> K8s API server는 별도 `kubernetes` provider 사용.

### OIDC Endpoints

| Endpoint | URL |
|----------|-----|
| Issuer (K8s) | `https://authentik.local.narwhal.io/application/o/kubernetes/` |
| Issuer (APISIX) | `https://authentik.local.narwhal.io/application/o/apisix/` |
| Authorization | `https://authentik.local.narwhal.io/application/o/apisix/authorize/` |
| Token | `https://authentik.local.narwhal.io/application/o/token/` |
| UserInfo | `https://authentik.local.narwhal.io/application/o/userinfo/` |
| JWKS | `https://authentik.local.narwhal.io/application/o/apisix/jwks/` |
| Discovery (K8s) | `https://authentik.local.narwhal.io/application/o/kubernetes/.well-known/openid-configuration` |
| Discovery (APISIX) | `https://authentik.local.narwhal.io/application/o/apisix/.well-known/openid-configuration` |

### Scope Mappings

| Scope | 내용 |
|-------|------|
| `openid` | 기본 (sub 클레임) |
| `profile` | preferred_username, name |
| `email` | email |
| `groups` | 커스텀 PropertyMapping: `return [g.name for g in request.user.ak_groups.all()]` |

---

## K8s OIDC 토큰 테스트

```bash
# kubectl-oidc-login 플러그인 필요: kubectl krew install oidc-login
kubectl oidc-login get-token \
  --oidc-issuer-url=https://authentik.local.narwhal.io/application/o/kubernetes/ \
  --oidc-client-id=kubernetes \
  --oidc-extra-scope=groups \
  --certificate-authority=/etc/kubernetes/pki/ca.crt

# 발급된 토큰으로 API 서버 접근 테스트
KUBECONFIG=/dev/null kubectl \
  --server=https://192.168.56.100:6443 \
  --certificate-authority=/etc/kubernetes/pki/ca.crt \
  --token="<OIDC_TOKEN>" \
  auth whoami
```

---

## 비밀번호 변경 방법

### Authentik Admin UI에서

1. https://authentik.local.narwhal.io/if/admin/ 접속
2. Directory → Users → 사용자 선택
3. Set Password 클릭

### API로

```bash
BOOTSTRAP_TOKEN=$(kubectl get secret authentik-bootstrap-secret -n iam \
  -o jsonpath='{.data.bootstrap_token}' | base64 -d)

USER_ID=$(curl -sf http://authentik-server.iam.svc.cluster.local:9000/api/v3/core/users/?username=dev \
  -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" | jq -r '.results[0].pk')

curl -sf -X POST \
  http://authentik-server.iam.svc.cluster.local:9000/api/v3/core/users/${USER_ID}/set_password/ \
  -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"password":"newpassword"}'
```

---

## 구현 현황

현재 `11-2-authentik-config.sh`에는 기존 4개 그룹(cluster-admin, developer, viewer, guest)만 생성됩니다.
`infra-admin`, `platform-admin` 그룹 및 사용자는 다음 단계에서 추가 예정입니다.

| 그룹 | 구현 상태 |
|------|-----------|
| `cluster-admin` | ✅ 완료 |
| `developer` | ✅ 완료 |
| `viewer` | ✅ 완료 |
| `guest` | ✅ 완료 |
| `infra-admin` | ⬜ 미구현 (계획됨) |
| `platform-admin` | ⬜ 미구현 (계획됨) |
