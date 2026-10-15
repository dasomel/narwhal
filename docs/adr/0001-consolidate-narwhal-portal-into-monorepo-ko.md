# ADR-0001: narwhal-portal을 narwhal 모노레포로 통합

- 상태: Proposed (import PR이 머지되면 Accepted)
- 날짜: 2026-10-15
- 대체: —
- 대체됨: —
- English: [0001-consolidate-narwhal-portal-into-monorepo.md](0001-consolidate-narwhal-portal-into-monorepo.md)

결정 ID: D-MONO-1(이력 보존 import), D-MONO-2(seam 검사는 저장소 내부 트리를 읽음),
D-MONO-3(CI), D-MONO-4(릴리스·이미지 정체성), D-MONO-5(이 ADR). import와 seam/문서 배선은
`feat/import-portal-seam` 브랜치에 있다.

## 배경

`narwhal`(Kubernetes IDP 클러스터: Bash/Helm/GitOps)과 `narwhal-portal`(Next.js 16 / React 19
관리 UI)은 Git 저장소가 아닌 umbrella 워크스페이스로 묶인 두 저장소였다.

- **포털은 독립 제품이 아니라 Narwhal의 UI다.** narwhal이 Deployment에 주입하는 환경변수 57개 중
  53개를 읽고, README가 Narwhal의 UI로 정의하며, `.narwhal.internal` 도메인이 하드코딩돼 있다.
- **작업이 공유된다.** 포털 이슈 64건 중 40건이 narwhal 이슈에서 파생됐고, 저장소 경계를 넘은
  인시던트가 15건이다.
- **seam이 pin 하나로 유지됐고 그 pin이 낡았다.** narwhal은 `ghcr.io/dasomel/narwhal-portal:1.0.19`를
  고정하는데, 포털 `main`은 이미 Valkey TLS+ACL 환경변수를 요구하고 `13-2-narwhal-portal-bindings.sh`는
  아직 주입하지 않는다(Valkey 변경은 PR #343에 대기). 포털 bump와 그에 맞는 클러스터 변경을 원자적으로
  반영할 수 없다. beluga가 118커밋 뒤처진 pin으로 겪은 것과 같은 실패다(beluga ADR-0005).
- **seam 검사가 선택적이었다.** R117, R55, R216, R211은 형제 체크아웃(`../narwhal-portal`)이나 pin된
  태그 트리와 비교했고, 없으면 조용히 스킵했다(lessons-log 2026-09-07: R117이 비교 없이 PASS).

## 결정

1. **D-MONO-1:** `narwhal-portal`을 전체 이력과 함께 `portal/`로 import한다(`git-filter-repo` 경로
   재작성, 415 커밋). 커밋 메시지의 PR 참조는 narwhal 번호와 혼동되지 않도록
   `dasomel/narwhal-portal#N`으로 한정한다. 태그는 `portal/vX.Y.Z`로 네임스페이스화한다. import된
   트리는 `portal@39c0079`와 동일함을 검증했다.
2. **D-MONO-2:** seam 검사는 저장소 내부 `portal/` 작업 트리를 읽는다. pin된 태그, 형제 체크아웃,
   네트워크가 없다. `check-oidc-rbac-portal-contract.sh`와 `check-kaniko-tag-portal-contract.sh`는
   기본값이 `portal/`이고, 파일이 없으면 SKIP이 아니라 FAIL한다. R117/R117b, R55, R216, R211은 항상
   실행되며 R211에는 음성 테스트(R211b)를 추가했다. 각 검사가 단언하는 내용은 그대로다. 이미지 pin
   `ghcr.io/dasomel/narwhal-portal:1.0.19`는 게시된 산출물이므로 유지하고, R210은 활성 pin이 모두
   일치하는지 계속 요구한다.
3. **D-MONO-3:** CI는 `portal-*.yml` 워크플로로 경로 필터링하고, 모든 PR에 필수 체크가 존재하도록
   항상 실행되는 `Portal gate` 집계 job을 둔다. Dependabot에 `/portal` 항목을 추가한다. (워크플로
   파일은 이 마이그레이션의 CI 레인 소관이며 이 변경 집합에 포함되지 않는다.)
4. **D-MONO-4:** 이미지 이름은 `ghcr.io/dasomel/narwhal-portal`로 유지하고 Git 태그만
   `portal/vX.Y.Z`로 바뀐다. 포털은 클러스터와 독립적으로 릴리스한다.
5. **D-MONO-5:** 포털은 자체 `AGENTS.md`/`CLAUDE.md`, LICENSE, SECURITY, CODE_OF_CONDUCT를
   `portal/` 아래에 유지하며 루트로 옮기거나 복제하지 않는다.

## 결과

- 포털 변경과 그에 필요한 클러스터 변경(env, RBAC, route, Valkey)이 하나의 PR에서 리뷰되고, seam 게이트가
  나중의 clean install이 아니라 그 PR에서 실패한다.
- 저장소 전역 검사(lint, validate, docs, SAST)가 `portal/`도 본다. 포털이 자체 보유한 동등 검사와
  중복되는 검사는 범위에서 제외하되, 보안 스캔은 제외하지 않는다.
- 포털 pin(`1.0.19`)은 릴리스 산출물이므로 `portal/` HEAD보다 뒤처질 수 있다. 따라서 R211은 릴리스
  태그가 아니라 작업 트리의 probe route를 단언한다.
- lessons 규칙: 수정의 lessons-log 행은 그 수정이 건드린 영역의 로그(`portal/docs/lessons-log.md` 또는
  `docs/common/lessons-log.md`)에 쓰고, 양쪽에 걸친 수정은 narwhal 쪽에 쓴다.

- **다시 쓴 과거 태그는 의도적으로 푸시하지 않는다.** import에는 `portal/v1.0.0` ~ `portal/v1.0.19`가
  로컬로 들어 있지만, `portal-docker-publish.yml`이 `portal/v*.*.*`에서 트리거되므로 푸시하면 이미 배포된 이미지
  태그를 다시 빌드하고 서명한다. 릴리스는 다음 새 `portal/vX.Y.Z`부터 시작하며, 과거 태그는
  `dasomel/narwhal-portal`(아카이브하되 삭제하지 않음)에 남는다.
- 루트 `.gitignore`의 `logs/`, `secrets/`, `kubeconfig` 패턴이 같은 이름의 portal API 라우트 디렉터리와
  일치했다. `/portal/src/**/` 예외로 해당 소스를 추적하며, portal 소스가 다시 무시되면 R35가 실패한다.
- **Markdown 게이트 (D-MONO-CI-MD):** portal은 Markdown lint를 한 적이 없어, PR 기준(base) 대비로는 가져온
  portal의 모든 줄이 "새 줄"이 된다. 그래서 `scripts/ci/markdownlint-changed.py`는 `portal/` 파일을 import 기준
  커밋 `e8e8229d8adf350f57feb218f6c19233e2685df4`(HEAD의 조상일 때, 아니면 PR base) 대비로 측정한다. 레거시
  부채는 제외하지도, 문서를 고치지도 않는다: import 이후 바뀐 줄만 깨끗해야 한다. 정직한 한계: 레거시 부채는
  게이트에 보고되지 않는다. import 시점에는 1686건(MD034 687, MD040 664, MD032 137, MD022 121, MD031 42,
  MD058 11, 기타 15)이었고, 정리는 별도의 의도적 변경이다.

## 미완료 (아직 하지 않음)

- 포털의 열린 이슈 27건을 이 저장소로 이전하고 신구 번호 매핑 코멘트를 남긴다.
- 열린 PR은 옮길 수 없다. Dependabot PR은 재생성되고, 초안 #187과 #77은 다시 열어야 한다.
- PR 머지 후 `dasomel/narwhal-portal`을 `portal/` 안내와 함께 아카이브한다.
- 브랜치 보호의 필수 체크에 `Portal gate`를 반영한다.
- Valkey 변경(PR #343)과 portal v1.1.0 릴리스 순서를 정한 뒤 pin을 올린다.
- OpenForge 레지스트리의 별도 `narwhal-portal` 항목을 정리한다.

## 검토한 대안

- **두 저장소 유지 + 계약 검사 강화** — 기각: 원자적이지 않은 저장소 간 변경과 이미 낡은 수동 pin
  bump가 그대로 남는다.
- **두 프로젝트를 담는 새 umbrella 저장소** — 기각: 관리할 저장소가 하나 늘고 이력 재작성 비용은
  같다.
- **Git submodule** — 기각: 여전히 pin이고 기여자·CI에 불편하다.
- **squash import** — 기각: mistakes log가 참조하는 415커밋의 blame과 인시던트 이력을 잃는다.
