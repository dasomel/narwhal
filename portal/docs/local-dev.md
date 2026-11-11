# 로컬 개발 가이드 (Skaffold + Kaniko + pnpm)

## Clean Install (신규 환경 초기 설정)

1. `bash scripts/bootstrap-secrets.sh` — `AUTH_SECRET`, `VALKEY_PASSWORD`, `LIVE_INGEST_SECRET` 자동 생성, `.env.local` 신규 작성
2. `.env.local` 열어 placeholder 값 직접 채우기:
   - `OIDC_CLIENT_SECRET` / `KEYCLOAK_CLIENT_SECRET` — Keycloak 클라이언트 설정에서 발급
   - `KEYCLOAK_ADMIN_CLIENT_SECRET` — Keycloak SA 클라이언트 생성 후 입력
   - `OPENBAO_TOKEN` — OpenBao AppRole auth 후 발급 (로컬 전용 폴백; 클러스터 배포는 narwhal#156 이후 `OPENBAO_AUTH_METHOD=kubernetes`로 Kubernetes auth를 쓰며 이 값을 주입하지 않는다)
   - 자세한 절차: [docs/security-clean-install.md](./security-clean-install.md)
3. `pnpm install`
4. `pnpm dev` (로컬) 또는 `pnpm run dev:skaffold` (클러스터 내 HMR)

### ⚠️ 클린설치 후 첫 빌드: kaniko OOM 회피 (자주 발생)

> **✅ 검증됨 (2026-06-14)**: 아래 절차로 클린설치 직후 첫 빌드를 실제로 통과시켰습니다.
> skaffold `build.cluster`의 `nodeSelector`(narwhal-worker-2) + `tolerations`
> (`node.kubernetes.io/unschedulable:NoSchedule`) 조합을 skaffold가 정상 수락했고, kaniko Pod이
> cordon된 worker-2에 스케줄돼(`Successfully assigned ... to narwhal-worker-2`) `skaffold run`이
> 빌드+push를 완료했습니다(이미지 `dev-m-20260614-231209`).
>
> **선행 조건**: harbor pull이 되려면 worker 노드가 `*.local.narwhal.internal`를 master dnsmasq로
> resolve해야 합니다(`tls: unrecognized name` 방지). 클린설치는 `10-worker-dns.sh`가 자동
> 처리하지만, 수동 첫 빌드 전이라면 worker DNS가 설정돼 있는지 먼저 확인하세요.

클린설치(클러스터 재생성) 직후에는 Harbor의 kaniko 레이어 캐시(`library/narwhal-portal-cache`)가
비어 있어, 첫 `skaffold run` / `dev:skaffold`가 node_modules 전체를 스냅샷합니다
(피크 ~3.5–3.9GB anon RSS). worker VM이 8GB(`WORKER_MEMORY`, 2026-11-08 전에는 6GB)이고 기본 워크로드가 ~2.5GB를
쓰므로, 첫 풀빌드 kaniko Pod이 **전역 OOM(dmesg `constraint=CONSTRAINT_NONE`)** 으로 죽어
`narwhal-portal` Pod이 `ImagePullBackOff`에 머뭅니다.

> **중요**: 이건 worker RAM이 부족해서가 아니라 *캐시가 없는 첫 풀빌드*만 무겁기 때문입니다.
> 캐시가 한 번 채워지면 이후 빌드는 증분이라 8GB worker(이전 6GB)에서 정상 동작합니다. 따라서 **RAM 영구
> 증설은 불필요**하고, 첫 빌드 한 번만 worker 한 대를 비워서 통과시키면 됩니다.

```bash
# 1) kubeconfig 갱신(클린설치로 CA가 바뀜) + kaniko 빌드 secret 준비
bash ../narwhal/scripts/common/set-config.sh
pnpm run harbor:setup           # devtools/idp-portal-kaniko-harbor + narwhal-root-ca-secret 생성

# 2) worker-2 비우기 (cordon + evict → 그 위 파드가 worker-1/3로 이동)
vagrant ssh master-1 -c "kubectl drain narwhal-worker-2 \
  --ignore-daemonsets --delete-emptydir-data --force --timeout=120s"

# 3) kaniko를 비워진 worker-2에 고정해 첫 빌드 (skaffold.yaml build.cluster에 임시로
#    nodeSelector: kubernetes.io/hostname=narwhal-worker-2 +
#    tolerations: node.kubernetes.io/unschedulable (NoSchedule, Exists) 를 추가)
skaffold run -f skaffold.yaml
git checkout skaffold.yaml       # 임시 변경 원복

# 4) 노드 복구
vagrant ssh master-1 -c "kubectl uncordon narwhal-worker-2"
```

첫 빌드가 Harbor에 `narwhal-portal-cache`를 채우고 나면, 이후 `pnpm run dev:skaffold`는 6GB
worker에서 OOM 없이 file sync/HMR로 동작합니다.

(영구 해결을 원하면 Vagrantfile `WORKER_MEMORY`를 8–10GB로 올릴 수도 있습니다 — 호스트 RAM
여유가 충분할 때만. 다만 위 캐시 워밍업 방식으로 충분하므로 권장하지 않습니다.)

---

Skaffold가 Kaniko를 이용해 **Narwhal 클러스터 안에서 이미지를 빌드**하고, Harbor에 push한 뒤 `devtools/idp-portal` Deployment를 갱신합니다. 로컬 Docker/Docker Desktop이 필요하지 않습니다.

> **패키지 매니저: pnpm** (`packageManager: pnpm@10.27.0`, `pnpm-lock.yaml` 커밋됨 — 지원되는 유일한 플로우).
> `Dockerfile`/`Dockerfile.dev`는 `bun.lock`이 존재하면 빌드 시 Bun(1.3.13+)을 자동 사용하도록
> 되어 있으나, 이 저장소는 `bun.lock`을 커밋하지 않으므로 실제로는 항상 pnpm 경로를 탄다.

---

## 동작 방식

```
코드 수정
  ├─ (작은 변경) Skaffold file sync → 컨테이너 /app 갱신 → Next.js HMR
  └─ (큰 변경)  Kaniko Pod 빌드 → Harbor push → Deployment rollout
                      ↑
                 클러스터 안에서 실행
                      ↓
               devtools/idp-portal Service
                      ↓
          localhost:3000 ← port-forward 자동
```

- Next.js는 **클러스터 안에서** 실행됩니다 (로컬 `pnpm dev` 불필요)
- 이미지 빌드도 클러스터 안에서 (**로컬 Docker 불필요**)
- Keycloak/Prometheus/Loki/ArgoCD/Valkey는 pod 내부 DNS로 직접 접근 → port-forward 불필요
- ArgoCD는 `image`/`env` 필드 drift를 무시 (`gitops/apps/idp-portal.yaml` `ignoreDifferences`) — Skaffold와 GitOps 공존
- 이미지 태그: `dev-{USER}-{YYYYMMDD-HHMMSS}` — 매 빌드 고유

---

## 사전 준비

### 1. Skaffold + pnpm 설치

```bash
brew install skaffold
skaffold version   # v2.x 이상

corepack enable
pnpm --version     # 10.27.0 (package.json packageManager 고정값)
```

> 의존성 변경 시 `pnpm install` 실행 (`pnpm-lock.yaml` 갱신).

### 2. kubeconfig 설정

```bash
vagrant ssh master-1 -c "sudo cat /etc/kubernetes/admin.conf" > ~/.kube/narwhal.yaml
sed -i '' 's|https://.*:6443|https://192.168.56.100:6443|' ~/.kube/narwhal.yaml

export KUBECONFIG=~/.kube/narwhal.yaml
kubectl config rename-context kubernetes-admin@kubernetes narwhal
kubectl config use-context narwhal
kubectl get nodes
```

> VIP ping 안 되면: `sudo route delete -host 192.168.56.100`

### 3. Kaniko 빌드용 Secret 주입 (1회)

클러스터에 Harbor push 자격과 Narwhal CA를 등록합니다:

```bash
pnpm run harbor:setup
# = bash scripts/harbor-kaniko-setup.sh
```

스크립트 동작:
1. `devtools/harbor-secrets` → `HARBOR_ADMIN_PASSWORD` 조회
2. `docker config.json` 생성 → Opaque Secret `idp-portal-kaniko-harbor` (devtools)
3. `platform-system/narwhal-root-ca-secret` → `devtools/narwhal-root-ca-secret`로 복제 (Kaniko pod이 같은 namespace의 Secret만 mount 가능)

Harbor 관리자 비밀번호가 회전되거나 CA가 갱신되면 재실행.

### 4. Secret 확인

`idp-portal-secrets`(앱 런타임 환경변수)는 GitOps가 이미 생성. 누락 여부만 확인:

```bash
kubectl -n devtools get secret idp-portal-secrets
```

---

## 개발 워크플로우

### 일반 개발 (HMR)

```bash
cd portal
make dev-cluster          # = pnpm run dev:cluster = scripts/dev-cluster.sh
```

`dev:skaffold`(래퍼 없는 `skaffold dev`)는 생성된 매니페스트가 없으면 일부러 실패한다.
전체 절차(Harbor 로그인, 메모리, 종료·원복)는 [LIVE-DEV-container-reload.md](LIVE-DEV-container-reload.md) 경로 B.

실행 흐름:

1. `scripts/dev-cluster.sh`가 GitOps 차트에서 `narwhal-portal` Deployment만 렌더 (image/resources/startupProbe만 변경)
2. 로컬 Docker가 `Dockerfile.dev`를 빌드해 Harbor(`harbor.local.narwhal.internal/library/narwhal-portal:dev-...`)에 push
3. 생성된 Deployment만 적용 → 이미지 교체 (Valkey/Service/Secret은 ArgoCD 관리 그대로)
4. `narwhal-portal` Service 3000 → `localhost:3000` port-forward
5. `src/**`, `public/**`, `next.config.ts` 변경 감지 → 컨테이너 `/app`으로 파일 동기화 → Next.js HMR

브라우저: **http://localhost:3000**

종료: `Ctrl+C` (`--cleanup=false`라 배포는 유지됨 - 원복은 LIVE-DEV 문서 참고)

### 디버깅 (Node Inspector)

```bash
pnpm run debug:skaffold
```

- 배포 직후 `NODE_OPTIONS=--inspect=0.0.0.0:9229` env 주입
- `localhost:3000` + `localhost:9229` port-forward

#### IntelliJ IDEA Ultimate Attach

1. **Run → Edit Configurations → + → Attach to Node.js/Chrome**
2. Host: `localhost`, Port: `9229`
3. Run → **Debug '...'**

#### VS Code Attach

`.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "node",
      "request": "attach",
      "name": "Attach to Skaffold",
      "address": "localhost",
      "port": 9229,
      "localRoot": "${workspaceFolder}",
      "remoteRoot": "/app",
      "skipFiles": ["<node_internals>/**"]
    }
  ]
}
```

### 1회 배포 (dev 이미지)

```bash
pnpm run deploy:skaffold
# = skaffold run
```

빌드 + 배포 후 종료. watch/port-forward 없음.

### 운영 이미지 수동 배포

GitOps가 기본이지만 긴급 수동 배포가 필요하면:

```bash
# Skaffold 없이 로컬 docker로 빌드/푸시 (Docker Desktop 필요)
make all
kubectl -n devtools rollout restart deployment/idp-portal
```

---

## IntelliJ Cloud Code 통합 (선택)

JetBrains Marketplace에서 **Cloud Code** 플러그인 설치:

- `skaffold.yaml` 자동 인식
- Run/Debug Configurations에 `Develop on Kubernetes`, `Debug on Kubernetes` 자동 생성
- 빌드 로그, 배포 상태, port-forward 상태를 IDE 통합 패널에서 확인
- 디버거 어태치 자동화 (debug 프로필 시 9229 auto-attach)

설정:
1. Preferences → Plugins → `Cloud Code` 설치 + IDE 재시작
2. Run → Edit Configurations → `+` → Cloud Code: Kubernetes → Develop
3. Deployment type: `Skaffold`, Skaffold config: `skaffold.yaml` 자동 선택

---

## 파일 구조

| 파일 | 역할 |
|------|------|
| `skaffold.yaml` | Skaffold + Kaniko 설정. dev(기본)/debug 프로필 |
| `Dockerfile` | 운영 이미지 (multi-stage, standalone) |
| `Dockerfile.dev` | 개발 이미지 (`pnpm dev`, HMR) |
| `scripts/harbor-kaniko-setup.sh` | Kaniko 인증 Secret 주입 (1회) |
| `Makefile` | 수동 `make build / push / all` (Skaffold 미사용 시) |
| `../gitops/resources/idp-portal-k8s.yaml` | Skaffold가 재사용하는 Deployment/RBAC/Valkey 매니페스트 |
| `../gitops/apps/idp-portal.yaml` | ArgoCD Application — `ignoreDifferences`로 dev 이미지 drift 무시 |

---

## 트러블슈팅

### Kaniko Pod이 `ImagePullBackOff` (base image `node:22-alpine`)

Docker Hub rate limit. `docker.io/library/node` 이미지가 당겨지지 않을 수 있음. 클러스터 노드에서 재시도:

```bash
kubectl -n devtools delete pod -l skaffold.dev/run-id  # 빌드 pod 재시도
```

장기 대응: Harbor를 Docker Hub 프록시로 설정하거나 Harbor에 base image 미러링.

### Kaniko push 시 `x509` 에러

`narwhal-root-ca-secret`이 devtools에 복제 안 됐거나 ca.crt key 이름이 다름:

```bash
kubectl -n devtools get secret narwhal-root-ca-secret -o jsonpath='{.data}' | jq 'keys'
# ca.crt가 없으면 → pnpm run harbor:setup 재실행
```

그래도 실패하면 임시로 TLS 검증 건너뛰기 (skaffold.yaml `kaniko.skipTLS: true` 또는 `skipTLSVerifyRegistry: ["harbor.local.narwhal.internal"]`).

### Kaniko push 시 `UNAUTHORIZED`

Harbor 비밀번호가 회전됐을 가능성. setup 스크립트 재실행:

```bash
pnpm run harbor:setup
```

### 빌드 Pod이 `CreateContainerConfigError`

`idp-portal-kaniko-harbor` secret이 없음:

```bash
kubectl -n devtools get secret idp-portal-kaniko-harbor
# 없으면 → pnpm run harbor:setup
```

### 배포가 계속 원복됨 (ArgoCD selfHeal)

`gitops/apps/idp-portal.yaml`의 `ignoreDifferences`에 해당 필드 포함 확인. 추가 필드 무시가 필요하면 jsonPointers에 경로 추가:

```yaml
ignoreDifferences:
  - group: apps
    kind: Deployment
    name: idp-portal
    jsonPointers:
      - /spec/template/spec/containers/0/image
      - /spec/template/spec/containers/0/env
      - /spec/replicas   # 예: 복제본 수도 바꾸면 추가
```

### HMR 반영 안 됨

Skaffold 콘솔에서 `Syncing 1 file(s) to ...` 로그 확인. 파일 경로가 `skaffold.yaml`의 `sync.manual` 패턴에 매치되지 않을 수 있음. 패턴 확장 후 재시작.

### `unable to connect to the server`

VIP 라우팅 문제:

```bash
ping -c 1 192.168.56.100 || sudo route delete -host 192.168.56.100
kubectl --server=https://192.168.56.10:6443 get nodes   # 개별 master 우회
```

### 세션 쿠키 오류 (`http: named cookie not present`)

Istio ambient mesh가 쿠키를 손상시킴. 매니페스트 pod label `istio.io/dataplane-mode: "none"` 유지 확인 (이미 설정됨).

---

## ArgoCD 관련 주의사항

- Skaffold는 `gitops/resources/idp-portal-k8s.yaml` (GitOps와 동일 파일)을 재사용합니다. image/env 외 다른 필드(리소스, 복제본 등)를 로컬에서 수정하면 **ArgoCD가 원복**합니다.
- 영구 변경이 필요하면 GitOps repo에 push해야 합니다.

---

## 참고

- Skaffold Kaniko builder: https://skaffold.dev/docs/builders/builder-types/kaniko/
- Kaniko 공식: https://github.com/GoogleContainerTools/kaniko
- Cloud Code for IntelliJ: https://cloud.google.com/code/docs/intellij/
- ArgoCD `ignoreDifferences`: https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/

## Mandatory regression status (narwhal#359)

Inventory of this tree (2026-11-12). `covered` means an offline regression exists,
not that live behavior was verified. Paths below are relative to `portal/` except
`.github/`. This update adds tenant-derived view/cache and rendered navigation tests;
production/auth/RBAC behavior is unchanged.

| Acceptance criterion | Covering tests / CI | Status |
|---|---|---|
| Deterministic test / E2E / typecheck / lint commands | `package.json`, `vitest.config.ts`, `playwright.config.ts`; `.github/workflows/portal-test.yml` | partial — commands/fixtures exist; default auth-test startup timeout observed below |
| Critical authz boundaries and negative cases | `src/lib/auth.test.ts`, `auth.fail-closed.test.ts`, `role-filter.test.ts`; `src/app/api/catalog/route.test.ts`, `catalog/[name]/route.test.ts`, `my-apps/route.test.ts`, `settings/users/route.test.ts`; `src/components/nav.test.tsx`; `e2e/auth-boundary.spec.ts` | partial — mapped-team boundaries covered; label-only developer leak below; real OIDC remains external |
| Canonical event / idempotency / operation state | `src/types/event-envelope*.test.ts`; `src/lib/idempotency.test.ts`, `operation-context.test.ts`; `src/app/api/events/ingest/route.test.ts`, `events/operation-e2e.test.ts` | covered |
| Browser login → effective permissions → scoped view → approved mutation → verified result | `e2e/team-scoped-catalog-mutation.spec.ts`, `admin-catalog-mutation.spec.ts`, `auth-boundary.spec.ts`; `e2e/fixtures/` | partial — full sequence with development login and provider read-back fixtures; real IdP/provider result needs live verification |
| Failure injection: timeout / 5xx / Valkey outage / stale cache | `src/lib/dependency-health.failure-injection.test.ts`, `http-client.test.ts`, `valkey.test.ts`, `live-outage-recovery.test.ts`; `src/app/api/health/dependencies/route.cache.test.ts` | covered — injected failures, not real service outages |
| Replica / failover / informer event duplication | `src/lib/live-k8s-informer.test.ts`, `k8s-event-resync.test.ts`, `live-outage-recovery.test.ts`, `live-stream.test.ts` | partial — lease takeover, watch retry, replay and duplicates simulated; real multi-Pod failover needs cluster |
| CI blocks merge on critical regression failure | `.github/workflows/portal-test.yml`; `scripts/check-regression-results.mjs`, `check-regression-results.node.mjs` | partial — fail-closed workflow exists; owner must confirm required branch-protection context `Portal gate` |
| Offline reproducible fixtures, no production credentials | `vitest.config.ts`, mocked providers in route tests; `playwright.config.ts`, `e2e/fixtures/` | covered — suite fixtures require no production credentials |

Required-coverage detail from the issue (same status convention):

| Required coverage | Covering test file(s) | Status |
|---|---|---|
| RBAC positive / negative | `src/lib/auth.test.ts`; `src/app/api/settings/users/route.test.ts`; `e2e/auth-boundary.spec.ts` | covered |
| Cross namespace / team / cluster visibility | `src/lib/role-filter.test.ts`, `cluster-registry.test.ts`; `src/app/api/my-apps/route.test.ts`, `catalog/route.test.ts`, `domain/clusters/route.test.ts`; `e2e/team-scoped-catalog-mutation.spec.ts` | partial — static-mapped tenant views covered; label-only leak below; real second-cluster data plane unverified |
| Canonical event schema / idempotency / ingest abuse | `src/types/event-envelope*.test.ts`; `src/lib/idempotency.test.ts`; `src/app/api/events/ingest/route.test.ts` | covered |
| Operation / correlation; accepted vs verified | `src/lib/operation-context.test.ts`; `src/app/api/events/operation-e2e.test.ts`, `catalog/[name]/sync/route.test.ts`, `catalog/[name]/rollback/route.test.ts` | covered |
| Namespace ownership | `src/lib/namespace-ownership.test.ts`; `src/app/api/namespaces/route.test.ts` | covered |
| ArgoCD sync / rollback authorization | `src/app/api/argocd/sync/route.test.ts`, `catalog/[name]/sync/route.test.ts`, `catalog/[name]/rollback/route.test.ts` | covered |
| Alertmanager silence scope | `src/lib/alert-silence-scope.test.ts`; `src/app/api/alerts/silence/route.test.ts` | covered |
| Privileged node tuning safety | `src/lib/tuning-approval.test.ts`, `tuning-commands.test.ts`; `src/app/api/nodes/[name]/tuning/apply/route.test.ts` | covered — mocked execution |
| Certificate / user lifecycle authz | `src/app/api/settings/certs/renew/route.test.ts`, `settings/users/route.test.ts`, `settings/users/[pk]/route.test.ts` | covered |
| Degraded provider / freshness | `src/lib/dependency-health*.test.ts`; `src/app/api/health/dependencies/route.cache.test.ts` | covered |
| Valkey outage / replay / live informer failover | `src/lib/valkey.test.ts`, `live-outage-recovery.test.ts`, `live-k8s-informer.test.ts` | partial — offline simulation; live multi-replica outage still needed |
| Keycloak pagination / cache / effective permissions | `src/lib/keycloak-client.test.ts`, `cache-invalidation.test.ts`, `auth.test.ts`; `src/app/api/settings/effective-permissions/route.test.ts` | covered |
| Prometheus unknown / partial / multi-series | `src/lib/prometheus.test.ts`; `src/app/api/metrics/route.test.ts`, `metrics/range/route.test.ts` | covered |
| Template preview / apply after #358 | `src/lib/service-templates.test.ts`, `domain/service-template-provisioning.test.ts`; `src/app/api/templates/route.test.ts` | partial — preview/provisioning contract tests exist; owner must reconcile #358 workflow and approve real apply evidence |
| Production configuration validation | `src/lib/config.test.ts`, `build-credential-guard.test.ts`, `valkey-security.test.ts`, `openbao.tls.test.ts` | covered — offline configuration guards |

`portal-test.yml` runs on every PR. A change filter runs typecheck/lint/Vitest and
Playwright for `portal/` or portal-workflow changes; unrelated PRs explicitly skip
heavy jobs. `Portal gate` always runs and rejects failed/cancelled prerequisites or
an unreadable filter. `Portal critical regression gate` checks both suite outcomes;
`node --test scripts/check-regression-results.node.mjs` exercises failure cases.
Build and styling checks also exist in `.github/workflows/portal-build-check.yml`
and `portal-harness-rules.yml`; they do not prove production identity or providers.

Remaining evidence/decisions: live Keycloak login and first-visit CSRF race;
real provider mutation/read-back; real multi-replica informer/Valkey failover;
second-cluster visibility; owner confirmation of branch protection and #358 apply
scope. Current policy also intentionally permits namespace-less cluster alerts for
scoped sessions (`src/lib/scope.ts`) and all namespaces for unteamed developer/viewer
roles (`config/role-filter.json`); stricter tenant defaults require an owner decision.

Observed defect (test-only investigation; failing probe removed as requested): with
`groups=["developer"]`, `teams=["tenant-a-team"]` absent from `teamMappings`, and
namespaces `tenant-a` / `tenant-b` labeled `narwhal.io/team=tenant-a-team` /
`tenant-b-team`, `/api/my-apps` returned both namespace names instead of only
`tenant-a`. `src/lib/role-filter.ts:404` falls back to the developer wildcard in
`config/role-filter.json:4` when no static team mapping matches; resolution becomes
all-namespace before labels can restrict it. This is distinct from an unteamed
role default and leaves label-only tenant isolation **missing**. Owner/auth changes
are needed; no production fix or failing/skipped test is included here.

Local verification caveat: `pnpm test -- --maxWorkers=4 --testTimeout=30000`
passes a literal `--` to Vitest with this pnpm version, leaving the timeout at 5s.
On the final tree it reported 2,381 passed / 1 failed: the existing
`auth.fail-closed.test.ts:49` positive control timed out at 5s. Pass Vitest options
directly as `pnpm test --maxWorkers=4 --testTimeout=30000` to enforce those limits.
The direct-option run passed all 167 files / 2,382 tests (85.67s). The CI command
remains `pnpm test` and can still encounter this startup timeout.

No skipped/placeholder tests were added. Browser E2E is inventoried here, not run
by this update; mocked view tests do not substitute for that runtime evidence.

## OpenBao authentication fallback (#54)

Development keeps `OPENBAO_TOKEN` as the local fallback when no Kubernetes JWT is mounted.
Production prefers Kubernetes workload identity and rejects unknown auth modes and implicit
static-token fallback. If `OPENBAO_AUTH_METHOD=kubernetes`, a missing projected JWT fails
closed even when `OPENBAO_TOKEN` exists. A still-supported cluster legacy exception requires
explicit `OPENBAO_AUTH_METHOD=token`; this is the mode supplied only when Narwhal's
`ENABLE_LEGACY_OPENBAO_TOKEN=true` provisioner flag is selected. This exception remains a
long-lived credential path and does not satisfy full provider identity/rotation acceptance.
