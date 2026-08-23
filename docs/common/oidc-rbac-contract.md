# OIDC 그룹 클레임 ↔ Kubernetes RBAC ↔ ArgoCD ↔ Portal 인가 계약 (OIDC/RBAC Contract)

## 1. 요약 (Why This Exists)

이 문서는 Keycloak이 발급하는 `groups` 클레임 하나가 (1) Kubernetes API 서버의 RBAC
서브젝트, (2) ArgoCD의 Casbin 정책 그룹, (3) narwhal-portal의 `UserRole`이라는 서로 다른
세 소비자에게 각각 다른 형태로 도달하는 계약을 명문화한다. narwhal#163
"[P0][Security][IAM] Align Portal OIDC Group Claims with Kubernetes RBAC Group Bindings"는
이 계약이 문서화되어 있지 않아 발생한 이슈다 — Portal의 `ALLOWED_GROUPS`가 K8s RBAC
서브젝트(`oidc:cluster-admin` 등 접두사 포함)와 문자열이 다르다는 점이 코드만 봐서는
"불일치(버그)"인지 "의도된 계층 분리"인지 판별되지 않았다. 결론부터 말하면 **Portal
쪽이 맞다**: `oidc:` 접두사는 K8s apiserver가 토큰을 검증하는 시점에 붙이는
apiserver-side 변환이며, 토큰 자체에는 절대 포함되지 않는다. 아래 표와 절마다 그
근거를 파일:라인으로 못박는다.

## 2. 그룹 매핑 표

| Keycloak 원본 클레임 (`groups`) | apiserver 변환 후 RBAC 서브젝트 | ClusterRole | Portal `UserRole` |
| --- | --- | --- | --- |
| `cluster-admin` | `oidc:cluster-admin` | `platform-admin` (내장 `cluster-admin` 아님) | `cluster-admin` |
| `developer` | `oidc:developer` | `developer` (cluster-wide read-only) + `developer-workload-admin`(네임스페이스별 RoleBinding, ClusterRoleBinding 없음) | `developer` |
| `viewer` | `oidc:viewer` | `platform-viewer` | `viewer` |
| `guest` | *(바인딩 없음)* | *(K8s RBAC 권한 0)* | `guest` |

근거:

- **Keycloak 그룹 생성**: `scripts/cluster/11-2-keycloak-config.sh:166`
  `for group_name in cluster-admin developer viewer guest; do` — 4개 realm 그룹을
  bare name으로 생성한다. 접두사도 없고 team-group 같은 nesting도 없다.
- **그룹 클레임에 full path를 넣지 않음**: `scripts/cluster/11-3-keycloak-clients.sh:143`
  (클라이언트별 `groups` mapper 생성 시)와 `:671`(K8s Dashboard 클라이언트 전용)의
  `config={"full.path":"false", ...}`. 이 설정 덕분에 ID/access 토큰의 `groups`
  클레임은 항상 bare name이고, 앞에 `/`가 붙는 계층 경로가 되지 않는다.
- **apiserver 측 접두사 부여**: `scripts/cluster/11-4-keycloak-apiserver.sh:191-194`
  (마스터-1, kubeadm 매니페스트 직접 patch)와 `:238-241`(마스터-2/3, SSH를 통한
  동일 patch) — `--oidc-groups-claim=groups`, `--oidc-groups-prefix=oidc:`.
  **토큰은 `oidc:`를 절대 담지 않는다.** apiserver가 토큰을 검증하며 RBAC 평가
  직전에 각 그룹 이름 앞에 `oidc:`를 붙이는, 순수하게 apiserver 쪽 로컬 변환이다.
- **RBAC 바인딩**: `gitops/resources/rbac-policies.yaml`
  - `oidc:cluster-admin` → ClusterRole `platform-admin` (:320-337). 내장
    `cluster-admin`이 아니라 별도로 만든 최소권한 역할이다 — 주석(:17-20,
    :320-322)이 명시하듯 노드 조작·ClusterRole/ClusterRoleBinding 변경은
    허용하지 않는다.
  - `oidc:developer` → ClusterRole `developer`(:339-360)는 cluster-wide지만
    get/list/watch만 가진다(:156-196). 쓰기 권한은 `developer-workload-admin`
    (:198-245)으로 분리되어 있고, 그 역할은 **ClusterRoleBinding이 없다** — `dev`
    네임스페이스의 RoleBinding(:404-429)으로만 부여된다. 2026-08-20 사건
    (:136-140 주석)에서 cluster-wide write가 `iam`/`database`/`platform-system`
    파드에 exec를 허용했던 것을 이 분리로 막았다.
  - `oidc:viewer` → ClusterRole `platform-viewer`(:363-379).
  - `oidc:guest` 바인딩은 **존재하지 않는다**. `guest`는 K8s RBAC 관점에서
    권한이 0이며, 이건 실수가 아니라 설계다 — Portal 쪽에서만 의미를 갖는
    "미인증/최소 스코프 UI 사용자" 개념이다.
- **Portal 측 원본 클레임 소비**: `narwhal-portal/src/lib/auth.ts:22-27`의
  `ALLOWED_GROUPS`는 bare name(`cluster-admin | developer | viewer | guest`)만
  허용한다. **이것은 버그가 아니라 의도된 동작이다.** Portal은 K8s apiserver가
  아니라 Keycloak과 직접 OIDC로 통신하므로 apiserver의 `oidc:` 접두사를 절대
  보지 않는다. `ALLOWED_GROUPS`에 `oidc:` 접두사를 붙이면 오히려 실제 토큰
  클레임과 어긋나 전원 `guest`로 강등되는 회귀가 된다.

## 3. 역할 그룹 vs 팀 그룹 분리 원칙

`narwhal-portal/config/role-filter.json`에는 RBAC 역할 그룹과 **완전히 별개인**
`teamMappings` 개념이 있다 (`platform-team`, `frontend-team`). 이건 네임스페이스/
ArgoCD 프로젝트에 대한 **UI 가시성 스코핑(visibility scoping)** 전용이며, RBAC
role/권한 부여에는 절대 관여하지 않는다.

`narwhal-portal/src/lib/auth.ts`의 `sanitizeTeamGroups()`(:65-73)가 이 분리를
코드 레벨에서 강제한다 — `TEAM_GROUP_RE`에 매치하더라도 `ALLOWED_GROUPS`에 이미
있는 값(즉 role 그룹)은 team 그룹 목록에서 명시적으로 제외한다
(`!ALLOWED_GROUPS.has(g as UserRole)`). 두 그룹 집합은 상호 배타적으로 유지되며,
하나의 Keycloak 그룹 클레임이 role과 team 두 가지 의미를 동시에 가질 수 없다.

## 4. ArgoCD 매핑

`gitops/resources/argocd-projects.yaml`과 `scripts/cluster/13-argocd.sh`를 실제로
읽어 확인한 내용이며, **ArgoCD도 Portal과 동일한 계층에 있다** — Keycloak과 직접
OIDC로 통신하고 K8s apiserver를 거치지 않으므로 `oidc:` 접두사를 보지 않는다.

- `13-argocd.sh:298-321`의 `argocd-rbac-cm` ConfigMap이 Casbin 정책의 근원이다.
  `scopes: '[groups]'`로 OIDC `groups` 클레임을 그대로 읽고, `g` 라인은 **bare
  name**을 ArgoCD 내장 role에 매핑한다:
  ```
  g, cluster-admin, role:admin
  g, developer, role:developer
  g, viewer, role:readonly
  g, guest, role:none
  ```
  `p` 라인은 `role:developer`에 `tenants/*` 프로젝트 sync만 허용하고(`applications,
  sync, tenants/*, allow`), `get`/`logs`는 전체(`*/*`)로 넓게 열어둔다 — 배포는
  좁게, 가시성은 넓게 라는 원칙이며 주석(:290-299)이 그 이유를 "developer가
  argocd/keycloak/kyverno 같은 플랫폼 Application까지 sync할 수 있었던" 과거
  사고로 설명한다.
- `argocd-projects.yaml`의 `tenants` AppProject(:78-128)는 별도의 프로젝트 스코프
  role을 하나 더 정의한다: `deploy` role이 `tenants/*` 애플리케이션의 get/sync를
  허용하고, `groups: [developer]`(:128-129) — 이 역시 bare name이다.
- 즉 ArgoCD 인가는 두 겹이다: (a) `argocd-rbac-cm`의 전역 role(`role:admin` /
  `role:developer` / `role:readonly` / `role:none`), (b) `tenants` AppProject 내부
  role(`deploy`). 둘 다 Keycloak `groups` 클레임의 bare name을 그대로 소비하며,
  K8s RBAC의 `oidc:` 접두사와는 무관한 별도 평가 경로다.

## 5. 토큰 갱신 / 그룹 변경 전파 시맨틱 (알려진 한계)

Portal은 NextAuth의 JWT 세션 전략을 쓴다. `groups` 클레임은 **최초 로그인 시점에만**
`profile` 콜백에서 읽어(`narwhal-portal/src/lib/auth.ts` jwt 콜백의
`rawGroups = p?.groups !== undefined ? p.groups : u?.groups` 및 그 아래
`token.groups = sanitizeGroups(rawGroups)`) 세션 JWT에 고정 저장된다.

Portal의 자체 토큰 갱신 로직 `refreshKeycloakToken`(`auth.ts` 내)이 만료 60초 전
access/id 토큰을 미리 갱신할 때는 `accessToken` / `expiresAt` / `refreshToken` /
`idToken`만 갱신하고 **`token.groups`를 새로 받아온 토큰의 클레임으로부터
재도출하지 않는다** — `rawGroups` 계산은 `profile`/`user` 객체가 존재할 때만
동작하는데, refresh-only 호출에는 이 값들이 없으므로 해당 블록 자체가
스킵되고 이전에 저장된 `token.groups`가 그대로 유지된다.

**실무적 함의**: Keycloak 관리자가 사용자의 그룹을 승격/강등해도, 그 변경은
**Portal 세션 JWT가 만료되어 사용자가 재인증(재로그인)하기 전까지 Portal에
반영되지 않는다.** 반면 K8s(`kubectl`)나 ArgoCD CLI는 사용자가 다음
토큰 교환을 할 때 바로 새 그룹을 본다 — 같은 Keycloak 그룹 변경이 소비자에
따라 반영 시점이 다르다는 뜻이다.

이 문서에서는 코드 수정을 하지 않고 **의도된 트레이드오프**로만 기록한다:
그룹 변경 후 즉시 반영되지 않으면 사용자에게 재로그인을 안내할 것. 자동
재검증을 원한다면 (a) 세션 TTL을 짧게 잡거나 (b) 매 요청마다 Keycloak
token-introspection을 호출하는 방식이 필요하며, 둘 다 이 문서 작성 시점
기준 미구현이다.

## 6. 미상 그룹 클레임 처리 (fail-closed)

토큰에 `ALLOWED_GROUPS`에 없는 미상 그룹 값이 들어오면 Portal은 **fail-closed**로
동작한다 — 그 값은 role 매핑에서 조용히 버려지고, 남은 유효 그룹이 없으면
`guest`로 강등된다. 이 로직은 `narwhal-portal/src/lib/auth.ts`에 이미 구현되어
있다: `unknownGroups()`가 거부된 원본 값을 추출하고, `classifyGroupClaim()`이
`ok` / `unknown_groups` / `no_groups`로 상태를 구분하며, jwt 콜백은
`groupClaimStatus === "unknown_groups"`일 때 `console.warn`으로 subject와 거부된
값을 남긴다 — "그룹 클레임이 아예 없는" 정상 케이스와 "있었지만 전부
거부된" 이상 케이스를 진단상 구분하기 위해서다.

관련 작업: 미상 그룹 클레임 fail-closed 처리, narwhal-portal#163.

## 7. 갭 / 향후 과제

- **Preflight 자동 검증 스크립트 부재**: 이 문서의 표(§2, §4)가 실제 배포된
  Keycloak 그룹, RBAC 바인딩, ArgoCD 정책과 계속 일치하는지 자동으로 대조하는
  스크립트가 없다. 별도 작업으로 진행 중이며, 이 문서는 그 스크립트가 검증할
  "정답"을 명문화하는 역할까지 겸한다.
- **라이브 e2e 검증 불가**: 이 문서 작성 시점 기준 narwhal 클러스터는 파괴된
  상태(`kakao-cluster-live` 메모 참고)라, 실제 Keycloak 로그인 → 토큰 발급 →
  apiserver RBAC 평가 → ArgoCD 로그인까지의 end-to-end 검증은 클러스터
  재기동 후에만 가능하다. 여기 기술된 매핑은 소스 코드/매니페스트 정적 분석
  결과이며, 런타임 관찰로 아직 재확인되지 않았다.
- `guest`에 대응하는 K8s RBAC 바인딩을 앞으로도 추가하지 않는다는 전제가
  코드 여러 곳(§2, §4)에 흩어져 있다 — 이 문서가 그 전제의 단일 근거가 된다.
