# narwhal 레포 디렉토리 구조 분석

> 작성: 2026-06-26 · 대상: `narwhal/` 레포 (origin `github.com/dasomel/narwhal`)
> 목적: 구조 이상 진단 + GitHub push 전 정리 판단 근거

## ✅ 적용 현황 (2026-06-26)

- **권장 #1 (idp/ history rewrite)**: 완료 — `git filter-repo --path idp/ --invert-paths` 로 전 히스토리(207커밋)에서 `idp/` 제거. 검증: `git log --all -- idp/` = 0커밋. 백업 번들 보관(scratchpad).
- **권장 #2 (.gitignore)**: 완료 — `test-results/`, `playwright-report/`, `*.webm`, `*.trace.zip` 추가.
- **권장 #3 (secret 로테이션)**: 미적용 — 운영값 여부 사용자 판단 필요(아래 표 값은 마스킹 처리).
- 본 문서 내 secret 값은 문서가 secret을 운반하지 않도록 마스킹함.

---

## TL;DR — 구조가 이상한 핵심 3가지

1. **한 레포 안에 IDP 구현이 둘** — 현행 Vagrant 기반(최상위)과 레거시 OpenTofu/vSphere 기반(`idp/`)이 공존.
2. **`idp/`(1130 파일)는 `.gitignore`에 있으나 여전히 추적 중** — 무시 등록 *전에* 커밋되어 인덱스에 그대로 박힘. 레포 용량·파일 수의 대부분을 차지.
3. **모든 하드코딩 자격증명과 33MB 테스트 산출물이 이 레거시 `idp/` 안에 있음** — push 시 public 노출 위험의 진원지.

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

## push 전 상태 확정 (검증 결과)

- origin/main 에 `idp/` 파일: **0개** (아직 안 올라감). 단독 5개 secret 파일도 origin에 없음.
- 그러나 `idp/`는 미push 89커밋 중 **딱 1개 커밋 `86a4953`** 에서 통째 유입 → 이 커밋이 secret blob을 운반.
- ⇒ **`git rm --cached idp/` + 새 커밋만으로는 불충분**: 과거 `86a4953` 이 그대로 push되어 secret이 GitHub 히스토리에 영구 기록됨.

## 권장 정리 (push 전, 우선순위순)

1. **history rewrite 로 `idp/` 제거** — 아직 origin에 아무것도 안 올라갔으므로 첫 push 전에 깨끗이 제거 가능:
   ```bash
   git filter-repo --path idp/ --invert-paths    # 89커밋 전 히스토리에서 idp/ 완전 삭제
   ```
   (filter-repo 미설치 시 `86a4953` 단일 커밋이므로 `git rebase -i` 로 해당 커밋 edit 후 `git rm -r --cached idp/` 도 가능 — 단 이후 커밋이 idp/를 다시 안 건드렸을 때만.)
   → 1130파일·33MB·하드코딩 secret이 push 대상에서 완전히 빠짐.
2. **테스트 산출물 패턴 무시** — `test-results/`, `*.webm`, `playwright-report/` 를 `.gitignore`에 추가(향후 재유입 방지).
3. **하드코딩 secret 로테이션** — rewrite로 신규 노출은 막히나, 실제 운영값이면 vCenter/SSH/Keycloak/PowerDNS 자격증명 **교체** 권장(이미 어딘가 노출됐을 가능성 가정).
4. **rewrite 후 재확인** — `git log origin/main..HEAD -- idp/` 가 비면 OK → push 여부 재판단.

> 주의: 본 문서는 **분석/제안만**. 실제 `git rm`·filter-repo·rebase·push 는 사용자 승인 후 진행. history rewrite는 커밋 해시를 바꾸므로 실행 전 백업 브랜치 권장.
