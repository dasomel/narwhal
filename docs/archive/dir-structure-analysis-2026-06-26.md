# narwhal 레포 디렉토리 구조 분석

> 작성: 2026-06-26 · 대상: `narwhal/` 레포 (origin `github.com/dasomel/narwhal`)
> 목적: 구조 이상 진단 + GitHub push 전 정리 판단 근거

## ✅ 적용 현황 (최종: 2026-06-27)

- **권장 #1 (idp/ history rewrite)**: ✅ 완료 — `git filter-repo --path idp/ --invert-paths` 로 전 히스토리(207커밋)에서 `idp/` 제거. 현재 HEAD `ea0420b`.
- **권장 #2 (.gitignore)**: ✅ 완료 — `test-results/`, `playwright-report/`, `*.webm`, `*.trace.zip` 추가.
- **dangling 객체 소거**: ✅ 완료 — `git reflog expire --expire=now --all` + `git gc --prune=now --aggressive` 로 secret 담은 도달불가 옛 커밋 물리 제거.
- **백업 번들**: ✅ 삭제 — rewrite 전 번들(scratchpad) 제거 → 옛 히스토리 복원 경로 없음(되돌릴 수 없음).
- **권장 #3 (secret 로테이션)**: ⏳ 미적용 — 운영값 여부 사용자 판단 필요(아래 표 값은 마스킹 처리).
- 본 문서 내 secret 값은 문서가 secret을 운반하지 않도록 마스킹함.

### 최종 검증 (2026-06-27)
| 항목 | 결과 |
|------|------|
| 히스토리·reflog `idp/` 커밋 | **0** ✅ |
| 작업트리 `idp/` | **없음** ✅ |
| secret 4종 (전체 객체 DB, unreachable 포함) | **0 객체** ✅ |
| dangling commit | **0** ✅ |
| remote (origin/gitea) | 정상, **push 안 함**(로컬 전용) |

---

## TL;DR — 구조가 이상한 핵심 3가지

1. **한 레포 안에 IDP 구현이 둘** — 현행 Vagrant 기반(최상위)과 레거시 OpenTofu/vSphere 기반(`idp/`)이 공존.
2. **`idp/`(1130 파일)는 `.gitignore`에 있으나 여전히 추적 중** — 무시 등록 *전에* 커밋되어 인덱스에 그대로 박힘. 레포 용량·파일 수의 대부분을 차지.
3. **모든 하드코딩 자격증명과 33MB 테스트 산출물이 이 레거시 `idp/` 안에 있음** — push 시 public 노출 위험의 진원지.

> 📌 아래 **1~4 절은 진단 시점(2026-06-26) 상태**입니다. 현재는 모두 처리됨 — 상단 "적용 현황" / 하단 "처리 이력" 참조.

---

## 1. 두 개의 병렬 프로젝트 (가장 큰 이상)

| 구분 | 최상위 (현행 narwhal) | 중첩 `idp/` (레거시) |
|------|----------------------|---------------------|
| 정체 | Vagrant 기반 K8s IDP 클러스터 | **"Hertz IDP"** — OpenTofu/vSphere 기반 IaC |
| IaC | `Vagrantfile` + `scripts/` | `idp/main.tf`·`provider.tf`·`variables.tf` (`tofu apply`) |
| 설치 | `scripts/cluster/*.sh` (00~15 단계) | `idp/install/addons/...` (별도 체계) |
| 추적 파일 수 | 약 200 | **1130 (레포 최대)** |
| git 등록 | 현행 작업 | 커밋 `86a4953` ("migrate from Authentik to Keycloak…")에서 통째 유입 |

→ 같은 목적(IDP 배포)의 **서로 다른 기술스택 구현 두 벌**이 한 레포에 들어 있어, 어느 쪽이 진짜 소스인지 혼동을 유발. 현행은 최상위, `idp/`는 사실상 아카이브/레거시로 보임.

## 2. `.gitignore`에 있으나 추적되는 모순

```
.gitignore:45  idp-portal/
.gitignore:46  idp/          ← 무시 대상이지만…
```

- `.gitignore`는 **이미 커밋된 파일을 untrack 하지 않음** → `idp/` 1130개가 그대로 추적 상태.
- 결과: `git status`는 깨끗해 보이지만 push하면 1130개 레거시 파일이 전부 GitHub로 올라감.

## 3. 커밋된 테스트 산출물 (불필요 bloat)

| 항목 | 수치 |
|------|------|
| `idp/tests` 디스크 | **33 MB** |
| `idp/tests/**/test-results/**` 추적 파일 | **234개** |
| 그중 png/webm/zip 등 바이너리 산출물 | **159개** |

- Playwright **e2e 실행 결과물**(스크린샷·영상·trace·error-context)이 그대로 커밋됨 — 원래 VCS에 들어가면 안 되는 빌드 산출물.
- 한글+특수문자 파일명(`…로그인-users-chromium/…`) 때문에 git이 경로를 따옴표 처리 → 경로 깨짐·이식성 저하.

## 4. 하드코딩 자격증명 위치 (전부 레거시 `idp/` 안)

> ⚠️ 아래 값은 **마스킹**됨 (이 문서 자체가 secret을 운반하지 않도록).

| 파일 | 값(마스킹) | 종류 |
|------|-----|------|
| `idp/docs/bak/MANUAL_DEPLOY_GUIDE.md` | `Cl******` | vCenter 비밀번호 |
| `idp/docs/troubleshooting/2026-03-23-incident-report.md` | `Keyc******` | Keycloak secret |
| `idp/install/addons/configure/configure-apiserver-oidc.sh` | `clo******` | 노드 SSH PW |
| `idp/install/addons/setup/setup-node-dns-ca.sh` | `clo******` | 노드 SSH PW |
| `idp/install/addons/install/install-repo-mirror.sh` | `powerdns-api-key-****` | PowerDNS API 키 |

→ 최상위 현행 `scripts/`는 전부 `="${VAR}"` 변수 주입(안전). 위험은 레거시 `idp/`에 집중.

## 5. 최상위 추적 디렉토리 (정상 구성)

```
scripts/      # 현행 설치/운영 스크립트 (cluster, common, airgap, test)
gitops/       # ArgoCD apps/resources/templates (현행 GitOps 소스)
configs/      # 클러스터 설정값
csp/          # 클라우드 프로바이더 연동 (kakao-cloud)
docs/         # 현행 아키텍처/운영 문서
poc/          # velero-rtk 등 실험
Vagrantfile, Makefile, README.md, VERSIONS.md, CHANGELOG.md, CLAUDE.md
```

### 추적되는 도구 상태(검토 대상)
- `.bkit/` (audit·snapshots·runtime·state), `.claude/`, `.github/` — 에이전트/CI 상태가 일부 커밋됨.

### 미추적 잡파일 (디스크에만, .gitignore로 제외됨 — 안전)
- `00.bak` (279MB), `graphify-out/` (캐시), `_workspace/`, `.codegraph/`, `*.log` — 모두 추적 안 됨.

---

## 처리 이력 (실행 완료)

진단 당시 상태: `idp/`는 미push 커밋 중 **딱 1개 커밋 `86a4953`** 에서 통째 유입돼 secret blob을 운반 → `git rm --cached` 만으로는 불충분(과거 커밋이 push 시 secret 운반)으로 판단, 아래 순서로 처리.

| 순서 | 작업 | 명령 | 결과 |
|------|------|------|------|
| 1 | rewrite 전 백업 | `git bundle create … --all` | 23M 번들(이후 삭제) |
| 2 | .gitignore 보강 | `test-results/`·`playwright-report/`·`*.webm`·`*.trace.zip` 추가 | ✅ |
| 3 | 정리 커밋 | `git add -A && git commit` (idp/ 삭제 + idp-portal) | ✅ |
| 4 | 히스토리 제거 | `git filter-repo --path idp/ --invert-paths --force` | 207커밋 재작성 ✅ |
| 5 | origin 재등록 | `git remote add origin …/dasomel/narwhal.git` | ✅ |
| 6 | dir.md secret 마스킹 | 분석 표 값 마스킹 + `git commit --amend` | ✅ |
| 7 | dangling 소거 | `git reflog expire --expire=now --all && git gc --prune=now --aggressive` | ✅ |
| 8 | 백업 번들 삭제 | `rm -f …/narwhal-pre-idp-rewrite.bundle` | ✅ |

**남은 작업**: 권장 #3(자격증명 로테이션) — 마스킹된 값들이 실제 운영값이면 vCenter/SSH/Keycloak/PowerDNS 교체. **push 여부는 미결정**(현재까지 로컬 전용, 원격 전송 없음).

> 주의: history rewrite 로 커밋 해시가 전부 바뀜. 백업 번들 삭제로 이 작업은 **되돌릴 수 없음**.
