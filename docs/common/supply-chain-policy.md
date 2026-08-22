# Narwhal / Narwhal-Portal 공급망 및 의존성 채택 정책 (Supply Chain Policy)

## 1. 수립 배경 (Why This Exists)

2026년 8월 발생한 Rust build-script 침해 사건이 이 정책의 직접적인 계기다. 당시 타사 패키지의 API를 직접 호출하지 않았음에도 `cargo build` 실행 과정에서 빌드 스크립트를 통해 악성 코드가 즉시 실행되었다.

'호환되는 최신 버전(latest compatible)'은 결코 신뢰 신호가 될 수 없다. narwhal 및 narwhal-portal 프로젝트는 외부 바이너리, npm 패키지, 컨테이너 이미지, Git Ref를 무조건 수용하지 않는다. 또한 실제 검증/차단이 강제화된 항목(`ENFORCED`)과 문서상 원칙에 불과한 항목을 엄격히 구분한다.

## 2. 통제 정책 및 강제화 현황 (Policy Rules & Enforcement Table)

두 저장소의 기본 쿨링 기간(Cooling window)은 7일이다.

| 검증 대상 / 영역 | 정책 내용 | 강제화 수단 (`Enforced by`) |
| --- | --- | --- |
| narwhal 린트 도구 버전 및 무결성 | `yq` `v4.53.3`, `kubeconform` `v0.8.0` 해시 검증 및 `markdownlint-cli@0.49.1` 고정 | `.github/workflows/lint.yml` (`sha256sum -c`로 `yq`, `kubeconform` 무결성 검증, `markdownlint-cli`는 무결성 해시가 없는 npm 특성상 버전 고정만 수행) |
| narwhal GitHub Actions Action 고정 | `.github/workflows/` 내 모든 `uses:` 지시자 커밋 SHA 고정 | `.github/workflows/` 내 모든 워크플로우 (40자리 hex commit SHA + `# vX.Y.Z` 주석) |
| narwhal 에어갭 바이너리 무결성 | 에어갭 번들에 포함된 19개 아티팩트의 SHA-256 검증 | `scripts/airgap/lib/binary-checksums.tsv` 및 `scripts/airgap/07-save-binaries.sh` (`fetch()` 시 검증, 누락 시 즉시 실패), `scripts/airgap/lib/refresh-binary-checksums.sh`로 갱신 |
| narwhal 외부 컴포넌트 커밋 고정 | `nfs-quota-agent` 커밋 SHA 고정 | `scripts/airgap/07-save-binaries.sh` (커밋 `387b057eec6aab7ebf7e26757e47dbb93a944307` 고정) |
| narwhal 정적 정밀 검사 | 동적/가변 다운로드 및 미고정 설치 금지 | `scripts/test/regression-check-kakao.sh --static` (CI `regression-static` 작업에서 `R50`~`R54` 검사) |
| narwhal 컨테이너 이미지 태그 | admission 차원에서 `*:latest` 태그 사용 금지 | `gitops/resources/kyverno-policies.yaml` (`disallow-latest-tag` 정책) |
| narwhal-portal npm 쿨링 기간 | 신규 도입 패키지 배포 후 7일 미만 경과 시 CI 실패 | `../narwhal-portal/scripts/check-lockfile-cooling.mjs` (`pnpm-lock.yaml`과 베이스 커밋 비교, npm registry API 조회, `COOLING_EXCEPTIONS` 사유 필수, CI `.github/workflows/license-and-sbom.yml`의 `cooling` 작업) |
| narwhal-portal 설치 스크립트 실행 | 어떤 의존성도 install script를 실행할 수 없음 | `../narwhal-portal/pnpm-workspace.yaml` (`onlyBuiltDependencies: []` — 빈 허용 목록을 명시해, 예외 추가가 리뷰에 보이는 diff가 되게 함) |
| narwhal-portal 잠금 파일 고정 | CI와 이미지의 모든 install이 잠긴 그래프만 설치 | `../narwhal-portal/scripts/check-supply-chain-policy.mjs` — 워크플로와 Dockerfile의 install 줄마다 `--frozen-lockfile`(bun은 `--ignore-scripts`까지)을 요구. **설정이 아니라 명령줄을 검사한다** (3절 3항 참고) |
| narwhal-portal 무결성 검증 | 위의 공급망 정책 통제 항목 자동 검증 | `../narwhal-portal/scripts/check-supply-chain-policy.mjs` (통제 항목 제거 시 실패) |

### narwhal 정적 검사 규칙 (`scripts/test/regression-check-kakao.sh --static`)

- `R50`: 빌드 경로 내 mutable `releases/latest/download` URL 사용 금지
- `R51`: 빌드 경로 내 mutable `archive/refs/heads` 또는 `refs/tags` 타르볼 사용 금지
- `R52`: 버전을 명시하지 않은 글로벌 `npm install` 사용 금지
- `R53`: 에어갭 다운로드의 체크섬 검증 수행
- `R54`: 체크섬 행 누락 시 스킵하지 않고 즉시 실패 처리

## 3. 의도적으로 사용하지 않는 기능 (Deliberately Not Used)

다음 기능들은 공급망 통제 목적으로 검토했으나 의도적으로 배제했다.

### 1) pnpm `minimumReleaseAge` 설정

`minimumReleaseAge`는 새로 추가되는 변경(Delta)이 아니라 전체 의존성 그래프를 **해상(resolution) 시점**에 제약한다. 이로 인해 이미 검토 및 승인되어 `pnpm-lock.yaml`에 존재하던 기존 전이 의존성(transitive dependency)이 풀릴 때 전체 설치가 차단된다.

- 실사례 실패 로그: `ERR_PNPM_NO_MATURE_MATCHING_VERSION Version 1.2.5 (released 3 days ago) of rolldown does not meet the minimumReleaseAge constraint / This error happened while installing the dependencies of vite@8.2.1`
- 결과: 이미 검토된 `rolldown` 1.2.5 버전 때문에 보안 업데이트를 포함한 13개의 Dependabot PR이 모두 실패했다.

### 2) Dependabot `cooldown:` 설정

Dependabot의 `cooldown:` 설정은 pnpm 적용 시 내부적으로 `--config.minimumReleaseAge=10080` 옵션을 추가하는 방식으로 구현되어 동일한 문제를 유발한다.

### 3) pnpm `frozenLockfile` 설정

같은 부류의 세 번째 스위치다. 잠금 파일을 **의도적으로 다시 만드는** 작업까지 포함해 모든 install에 걸리므로, 의존성 범프가 자기 자신의 `package.json` 수정 때문에 실패한다.

- 실사례 실패 로그: `ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with "frozen-lockfile" because pnpm-lock.yaml is not up to date with package.json / - @tanstack/react-query (lockfile: ^5.95.2, manifest: ^5.101.4)`
- 대체 수단: `check-supply-chain-policy.mjs`가 워크플로·Dockerfile의 install 줄에 `--frozen-lockfile`이 있는지 검사한다. 같은 보장을 런타임이 아니라 **리뷰 시점**에 주고, 잠금 파일을 바꿔야 하는 단 하나의 install은 막지 않는다.

### 핵심 교훈 (Lesson)

세 사례의 공통점은 셋 다 '델타'가 아니라 '모든 install/해상'에 거는 스위치였다는 것이다. 공급망 통제는 전체 의존성 상태(resolved state)가 아니라 **해당 변경이 새로 도입하는 델타**에 걸어야 하고, 그렇지 않으면 정상 유지보수 경로 — 특히 보안 업데이트 — 를 함께 잠근다. `onlyBuiltDependencies: []`만 설정으로 남은 이유도 이것이다: 그것은 install이 무엇을 *바꿀 수* 있는지가 아니라 의존성이 무엇을 *할 수* 있는지를 제약하므로 델타와 무관하다.

## 4. 미적용 및 한계 사항 (Not Enforced)

다음 항목들은 현재 통제 수단이 적용되지 않는 명확한 한계 영역(Gap)이다.

- CI/CD 빌드 작업의 아웃바운드 네트워크 통제 미적용 (이슈 #164에서 제한 요구사항 다룸).
- 여러 저장소 간 카나리/점진적 채택(Canary/progressive adoption) 메커니즘 부재.
- 이슈 #165에서 언급된 다른 저장소들(`Beluga`, `OpenForge`, `KubeMetal`, `kube-ready-box`, `ldapium`, `nfs-quota-agent`)은 본 문서의 통제 대상에 포함되지 않음.
- `Go`, `Rust`, `Java`, `Python` 생태계는 이 두 저장소(`narwhal`, `narwhal-portal`)에 존재하지 않으며 통제 수단 역시 없음.

## 5. 고정 도구 버전 변경 절차 (How to Bump a Pinned Tool)

고정된 도구 및 바이너리의 버전을 올릴 때 수행하는 작업 절차:

1. `.github/workflows/lint.yml`에서 해당 도구의 버전 변수와 `SHA256` 해시를 함께 수정한다.
2. `scripts/airgap/lib/refresh-binary-checksums.sh` 스크립트를 실행하여 `scripts/airgap/lib/binary-checksums.tsv` 테이블을 재생성한다.
3. 수정된 `git diff` 내용(버전 bump, SHA256 변경, tsv 갱신)을 리뷰 아티팩트로 제출한다.
