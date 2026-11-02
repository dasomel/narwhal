# 컨테이너 라이브 반영 (Live Dev) 가이드

> narwhal-portal 소스 변경을 클러스터(devtools)의 실행 중인 컨테이너에 반영하는 방법.
> 두 가지 경로가 있으며, 현재 **기본은 "prod 빌드+push"** 이고 **라이브 HMR은 ArgoCD
> selfHeal 충돌로 추가 설정이 필요**하다.
>
> 작성: 2026-06-09 · 대상: narwhal 클러스터(dev)

---

## TL;DR

| 방식 | 속도 | 안정성 | 현재 상태 |
|------|------|--------|-----------|
| **A. prod 빌드+push** | 느림(~2-4분) | 높음 (ArgoCD와 `:latest`로 호환) | ✅ 안정 경로 |
| **B. 라이브 HMR (skaffold dev)** | 즉시(파일 저장 시) | ✅ argocd-cm ignoreDifferences로 selfHeal 원복 차단 (2026-06-10) | ✅ **사용 가능** |

개발 중에는 **B**(즉시 HMR), 검증/마무리 시 **A**로 prod 이미지 확정.

---

## A. prod 빌드 + push (현재 기본 경로)

검증된 경로. ArgoCD가 `narwhal-portal`을 `:latest`로 관리하므로 같은 태그로 push하면 충돌 없음.

```bash
cd portal

# (최초 1회) harbor 로그인 — admin / devtools/harbor-secrets 의 HARBOR_ADMIN_PASSWORD

PASS=$(kubectl -n devtools get secret harbor-secrets -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
echo "$PASS" | docker login harbor.local.narwhal.internal -u admin --password-stdin

# 빌드 → push → 롤아웃

docker build -f Dockerfile -t harbor.local.narwhal.internal/library/narwhal-portal:latest .
docker push harbor.local.narwhal.internal/library/narwhal-portal:latest   # imagePullPolicy: Always
kubectl -n devtools rollout restart deploy/narwhal-portal
kubectl -n devtools rollout status  deploy/narwhal-portal --timeout=220s
```

- push 경로: 로컬 Docker daemon → APISIX 게이트웨이(`harbor.local.narwhal.internal` → 192.168.56.200) → Harbor.
- 큰 레이어가 **413**으로 막히면 harbor route의 APISIX `client-control` 한도 때문 → 아래 "100MB 제한" 참고.
- TLS는 narwhal CA를 Docker가 신뢰(로그인 성공 시 OK).

---

## B. 라이브 HMR (skaffold dev) — 파일 저장 즉시 반영

`src/**/*.{ts,tsx,js,jsx,css}`, `public/**`, `next.config.ts` 저장 → Skaffold file-sync로 컨테이너에
복사 → Next.js HMR. **재빌드 없이 즉시 반영.**

```bash
cd portal                      # 모노레포의 portal/ (옛 narwhal-portal 레포 아님)
# (최초 1회) Harbor 로그인 - 위 A 절의 docker login. 로컬 빌드 이미지를 push 한다.

make dev-cluster               # = pnpm run dev:cluster
# 종료: Ctrl+C (--cleanup=false 라 배포 유지)

```

`scripts/dev-cluster.sh`(D-PORTAL-DEV-FROM-CHART)는 손으로 유지하는 사본 대신 **GitOps 차트에서
`narwhal-portal` Deployment를 ArgoCD와 동일하게 렌더**(narwhal-apps의 valuesObject 사용)해
`deploy/.generated/skaffold-dev-portal.yaml`로 쓰고 `skaffold dev -p dev`를 실행한다. 바뀌는
필드는 딱 3개: `image`(Harbor dev 태그), `resources`(requests 1Gi / limits 3Gi, cpu 1),
`startupProbe`(`/api/health/live`, 5s x 60 - 첫 컴파일 동안 liveness 보류). Valkey, Service,
Secret, RBAC는 건드리지 않는다(ArgoCD 관리). kube context가 `narwhal`(로컬)이 아니면 거부한다
(`KUBE_CONTEXT`로 변경). 생성만 확인: `bash scripts/dev-cluster.sh --render-only`.
정적 검증은 narwhal R299.

스크립트가 시작 시 하는 일:

1. **argocd-cm 프리플라이트**: 라이브 `apps_Deployment` 값이 차트와 다르면 `kubectl patch cm argocd-cm`으로
   패치한다. argocd-config.yaml의 ConfigMap self-ignore가 이 키를 덮어서 git 수정은 기존 클러스터에
   닿지 않기 때문이다. ArgoCD 문서는 application-controller 재시작을 resource exclusions/custom labels에만
   요구하므로 기본은 재시작 없음; 그래도 dev Deployment가 되돌려지면 `ARGOCD_RESTART_CONTROLLER=1`.
2. **Harbor TLS 프로브**: `curl https://harbor.local.narwhal.internal/v2/`가 시스템 신뢰로 실패하면
   skaffold에 `--insecure-registry=harbor.local.narwhal.internal`을 붙이고 경고한다. skaffold의 digest 조회는
   Mac 키체인을 쓰므로(옛 클러스터의 'Narwhal IDP Root CA'가 남아 있으면 x509) 이 플래그는 **digest 조회에만**
   영향이 있고 `docker push`는 계속 `~/.docker/certs.d`로 검증한다. 정식 해결:

   ```bash
   mkdir -p ~/.docker/certs.d/harbor.local.narwhal.internal
   kubectl -n platform-system get secret narwhal-root-ca-secret -o jsonpath='{.data.ca\.crt}' | base64 -d \
     > ~/.docker/certs.d/harbor.local.narwhal.internal/ca.crt   # 이후 Docker Desktop 재시작
   ```

**왜 field manager인가 (D-PORTAL-DEV-FIELD-MANAGER)**: skaffold는 `--field-manager=narwhal-portal-dev`로
server-side apply하고, argocd-cm은 jq 경로 없이 `managedFieldsManagers: [narwhal-portal-dev]`만 둔다.
이전 `select(.image | test(":dev-"))` jq 가드는 ArgoCD가 jq를 target과 live **양쪽**에 적용하기 때문에 git target
(핀 이미지)에서는 아무것도 선택하지 않고 live에서만 필드를 지워 diff가 영구 비대칭이 되어 selfHeal이 수 초 내
되돌렸다. 비용: 그 매니저가 소유한 필드는 소유권이 돌아오기 전까지 ArgoCD에 보이지 않는다 -> 세션 후 `--restore`.

**메모리 예산**: `next dev`는 ~3Gi 필요(차트의 512Mi는 OOMKilled). 워커 6GB 기준 포털 pod가
requests 1Gi를 점유하므로 다른 무거운 워크로드와 같은 노드에 몰리지 않게 한다.

### B를 가능하게 한 전제조건 (이미 코드/설정에 반영됨)

라이브를 막던 요인들과 해결 — 모두 repo에 커밋되어 있어 재현 가능:

| 증상 | 원인 | 해결 (위치) |
|------|------|-------------|
| kaniko 빌드 OOMKilled | VM 노드 메모리(~5.9Gi)에서 in-cluster kaniko가 node_modules 스냅샷 시 OOM | dev 프로파일을 **로컬 Docker 빌드**로 (`skaffold.yaml` dev `build.local`) |
| harbor push 413 | APISIX `client-control` `max_body_size=100MB` | harbor route client-control **0**(무제한) — 런타임 etcd + gitea(`gitops/resources/apisix-routes.yaml`) |
| Skaffold sync 실패 `didn't sync any files` | Alpine **busybox tar**로는 file-sync 추출 불가 (GNU tar 필요) | dev base를 **Debian**(`node:22-slim`)으로 → GNU tar 포함 (`Dockerfile.dev`) |
| `next dev` EACCES `/app/.next/dev` | `/app`이 root 소유, non-root nextjs가 쓰기 불가 | `chown -R nextjs:nodejs /app` (`Dockerfile.dev`) |
| dev pod OOMKilled / 간헐 502 | `next dev`(Turbopack 상주)가 512Mi 초과 | dev 메모리 limit **3Gi** (`scripts/dev-cluster.sh`가 차트 렌더에 적용) |
| 로그인 `error=Configuration` | `package.json` dev 스크립트가 `NODE_EXTRA_CA_CERTS=./certs/...`(없는 경로)로 덮어써 Keycloak TLS 실패 | `"dev": "next dev"` (`package.json`) — deployment env(`/etc/ssl/narwhal/ca.crt`)가 적용되게 |
| deploy가 cross-ns RBAC에서 실패 / 사본 드리프트 | 차트 전체를 적용하면 security-system Role 등 다른 객체가 섞이고, 손 사본은 차트와 어긋남(Valkey 인증·projected token 누락) | 차트에서 Deployment 하나만 생성 (`scripts/dev-cluster.sh`, `skaffold.yaml` 기본 manifests) |

런타임 선행(클러스터 상태, 코드 아님):

```bash
# harbor 로그인 (위 A 참고)

# harbor route client-control 0 (etcd, 100MB 해제) — docs/archive/RELEASE-TODO-gitea-permanence.md 참고

```

### ✅ (해결됨 2026-06-10) ArgoCD selfHeal 원복 — argocd-cm ignoreDifferences

과거 차단 요인: **ArgoCD selfHeal이 skaffold dev의 dev 이미지(`dev-m-...`)를 수 초 내에 prod
`:latest`로 원복**해 HMR이 무효였다. selfHeal off는 app-of-apps 체인(`argocd-config` →
`narwhal-apps` → `narwhal-portal`)이 다시 켜버려 유지가 안 됐다.

**해결**: argocd-cm에 시스템 레벨 `ignoreDifferences`를 추가해 skaffold dev가 바꾸는 필드만
ArgoCD diff에서 제외 — selfHeal은 켜진 채로 dev 배포가 유지되고 앱은 Synced로 남는다.

- `apps_Deployment`: `managedFieldsManagers: [narwhal-portal-dev]` (jq 가드 아님 - 위 "왜 field manager인가")
- `all`: `skaffold.dev/run-id`, `app.kubernetes.io/managed-by` 라벨
- 런타임 patch 적용 + narwhal 로컬 커밋 `ab51121`(`gitops/resources/argocd-config.yaml`) —
  gitea 반영 전 argocd-cm 재생성 시 재적용 필요 (`docs/archive/RELEASE-TODO-gitea-permanence.md` 참고)

검증(2026-06-10, jq 가드 방식 - 이후 2026-11-02 라이브에서 selfHeal이 되돌리는 것이 확인되어 field manager 방식으로 대체됨; 2026-11-02 field manager 방식은 이미지 90초+ 유지, Synced): dev 이미지 배포 후 90초+ 유지, `narwhal-portal` 앱 Synced, file-sync로
`/app/src`에 즉시 반영, 포털 200 응답.

### 라이브 종료 후 prod 환원

`dev-cluster.sh`는 종료(Ctrl+C 포함) 시 trap으로 자동 복원한다. 수동/강제 종료 후에는:

```bash
bash scripts/dev-cluster.sh --restore
```

`--restore`는 차트(git)의 Deployment(핀 이미지)를 `--field-manager=argocd-controller --force-conflicts`로
server-side apply해 소유권을 돌려주고, `narwhal-portal-dev`가 단독 소유한 필드(startupProbe, skaffold 라벨)는
빈 apply로 해제한 뒤 ArgoCD hard refresh를 요청한다. 확인:

```bash
kubectl -n devtools get deploy narwhal-portal -o jsonpath='{.spec.template.spec.containers[0].image}'  # 핀 태그
```

---

## 빠른 sync 동작 검증

```bash
echo "export const __t=1" > src/__synctest.ts   # skaffold dev 가 떠 있을 때
# skaffold 로그에 "Syncing 1 files" + 에러 없이 "Watching for changes" → OK

POD=$(kubectl -n devtools get pods -l app=narwhal-portal --no-headers | grep 1/1 | awk '{print $1}' | head -1)
kubectl -n devtools exec "$POD" -- cat /app/src/__synctest.ts   # 내용 보이면 sync 성공
rm -f src/__synctest.ts
```

---

## 관련 문서

- `docs/archive/RELEASE-TODO-gitea-permanence.md` — 런타임 패치(client-control 0, selfHeal off)의 gitea 영구화
- `docs/local-dev.md` — kubeconfig / kaniko secret 셋업
- `docs/adr-skaffold-dev-workflow.md` — Skaffold+Kaniko 워크플로 ADR
