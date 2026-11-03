# OSS 업데이트 프로세스

플랫폼이 쓰는 오픈소스 컴포넌트를 새 버전으로 올리는 전체 흐름이다. 포털의 **설정 > OSS 업데이트** 탭(cluster-admin 전용)은 현황을 보여 주고 요청 문안을 만들 뿐, 클러스터를 직접 바꾸지 않는다. 실제 변경은 항상 PR → 독립 리뷰 → GitOps 경로를 지난다.

## 흐름

```
감지 → 요청 → 분류(triage) → 업그레이드 PR → 리뷰 → GitOps 반영 → 라이브 검증 → 종료
```

| 단계 | 누가/무엇이 | 산출물 |
|---|---|---|
| 1. 감지 | 포털 OSS 업데이트 탭, Dependabot(`.github/dependabot.yml`, GitHub Actions만), `version-check` CI, 담당자의 릴리스 노트 확인 | 올릴 후보와 목표 버전 |
| 2. 요청 | cluster-admin이 탭에서 "업그레이드 요청". 포털이 이슈 본문을 생성하고 감사 로그(`oss.update.request`)를 남긴다 | `dasomel/narwhal` 이슈 (본문 복사 또는 "새 이슈 열기") |
| 3. 분류 | 담당자가 이슈를 검토: 쿨링, 고위험 여부, 동결 사유(VERSIONS.md "Frozen"), 마이너 라인 고정 여부 | 진행/보류/거절 결정을 이슈에 기록 |
| 4. 업그레이드 PR | `narwhal-version-upgrade` 스킬 절차를 따른다 (아래) | PR |
| 5. 리뷰 | 작성자와 다른 독립 리뷰어. PASS 시 `scripts/ci/mark-review-pass.py <pr> --sha <40-hex>` | `independent-review` 성공 상태 |
| 6. GitOps | PR 머지 → Argo CD 동기화. 미러가 먼저 준비되어 있어야 한다 | Synced / Healthy |
| 7. 라이브 검증 | 앱이 보고하는 실행 버전이 목표와 같은지 확인 (차트 메타데이터 추론 금지) | 증거를 PR/이슈에 첨부 |
| 8. 종료 | 이슈 체크리스트 완료 후 닫는다. 실패·함정은 `docs/common/lessons-log.md`에 한 행 | 닫힌 이슈 |

## 포털 탭이 보여 주는 것

- **선언 버전**: `VERSIONS.md`의 값. 포털 이미지는 `portal/`만 빌드하므로 `portal/src/data/oss-versions.generated.json`(커밋된 스냅샷)을 읽는다. `VERSIONS.md`를 고치면 `node portal/scripts/gen-oss-versions.mjs`로 갱신해야 하며, `version-check` CI가 `--check`로 동기화를 강제한다. 포털이 배포된 이후의 `VERSIONS.md` 변경은 다음 포털 릴리스에 반영된다(포털 핀은 `portal/` HEAD보다 늦을 수 있다).
- **실행 버전**: Argo CD Application의 `targetRevision`(차트)과 Trivy 리포트에 잡힌 실행 이미지 태그. 기존 포털 접근만 쓰며 RBAC를 늘리지 않는다. 선언과 다르면 "불일치"로 표시한다.
- **업스트림 최신/차이/뒤처진 일수**: GitHub Releases(공개, 인증 없음). 기본은 **꺼짐**(에어갭 안전)이며 `OSS_UPDATES_UPSTREAM=true`일 때만 조회한다. 꺼져 있거나 조회가 실패하면 "알 수 없음"이다. 사용자·클러스터·버전 정보를 외부로 보내지 않고, 결과는 서버에서 6시간(실패는 10분) 캐시한다.
- **쿨링**: 릴리스 후 7일 미만이면 "쿨링 중"이며 요청이 거절된다(`portal/scripts/check-lockfile-cooling.mjs`와 같은 7일 규칙). 마이너 라인을 의도적으로 고정한 컴포넌트(Kubernetes, Keycloak, APISIX, Tempo)는 같은 라인의 최신을 보여 준다.
- **CVE**: Trivy가 이미 스캔한 *실행 중* 이미지의 Critical/High 합계. 목표 버전의 CVE는 아니다.

## 요청 모드

현재는 **복사 모드**가 기본이다. 이슈 트래커 토큰이 없으므로 포털은 이슈를 만들지 않고 본문을 생성한다. 같은 컴포넌트+목표 버전은 14일 동안 포털 안에서 한 번만 기록되고(중복 요청은 기존 요청 정보와 같은 본문을 돌려준다), GitHub의 열린 이슈는 포털이 볼 수 없으므로 등록 전에 사람이 검색한다.

토큰 모드(미구현, 별도 승인 필요)는 `dasomel/narwhal`에 `issues: write` 권한만 가진 fine-grained 토큰 1개와 이를 마운트할 Secret/배포 변경이 필요하다. 그때 열린 이슈 검색으로 완전한 멱등성을 얻는다. 이 변경은 cluster repo(GitOps) 소유이며 포털 단독으로 추가하지 않는다.

## 업그레이드 PR 체크리스트 (스킬 요약)

정본은 `.agents/skills/narwhal-version-upgrade/SKILL.md`이다. 생성되는 이슈 본문은 아래를 그대로 체크리스트로 싣는다.

1. 목표 릴리스가 7일 이상 지났고(쿨링), 두 아키텍처(amd64/arm64)를 지원하며, 브레이킹 변경(차트/설정/API/RBAC/스토리지)이 없는지 확인.
2. 버전·이미지·차트 참조를 함께 갱신. 미리 적재하는 아티팩트면 `scripts/airgap/images.txt`도. 가변 태그 금지.
3. 에어갭/Kakao: 핀이 라이브가 되기 **전에** 배스천 미러에 digest로 미러(`skopeo copy --all --preserve-digests`), `MIRROR=http://<bastion>:5000 python3 scripts/test/check-mirror-pins.py` 통과. 절차: `scripts/airgap/README.md`.
4. `VERSIONS.md` 동기화: `bash scripts/ci/check-version-consistency.sh`, `node portal/scripts/gen-oss-versions.mjs --check`. 호환성 표(Kubernetes/Cilium/csi-driver-nfs/Istio) 확인.
5. 고위험 경로(`gitops/**`, `VERSIONS*`, `scripts/airgap/**` 등, `.agents/evals/risk-policy.json`)를 건드리면 `.agents/evals/traces/`에 트레이스를 추가.
6. `make lint`, `make validate`, `make test`.
7. 독립 리뷰 PASS → 머지 → Argo CD 동기화 → 라이브에서 실행 버전 확인 → 이슈 종료.

## 하지 않는 것

- 포털에서 클러스터/Argo 앱을 바꾸는 "적용" 버튼은 없다.
- 쿨링 중인 버전을 예외로 올리려면 이슈에 소유자 승인을 기록한다(포털은 거절한다).
- 업스트림 조회를 켜도 인증 토큰은 쓰지 않는다. GitHub 비인증 한도(IP당 시간당 60회)를 넘으면 해당 행은 "알 수 없음"이 된다.
