# 0단계 — 정적 검증 (Static Validation)

VM 없이 리포지토리만으로 실행하는 검증이다. CI(`.github/workflows/lint.yml`)가 PR/push마다
실행하는 검사를 로컬에서 그대로 재현한다 — **커밋 전에 이 단계를 통과시키면 CI 실패로
되돌아올 일이 없다.**

> **언제 실행하나:** 스크립트/YAML/Vagrantfile을 수정했을 때, 커밋·PR 전, 그리고
> 1단계(프로비저닝)를 시작하기 전 항상.

## 사전조건 (도구 설치)

macOS 기준:

```bash
brew install shellcheck yq kubeconform helm
# ruby는 macOS 기본 탑재, python3도 기본 탑재
```

| 도구 | 용도 | 없으면 |
|------|------|--------|
| shellcheck | 쉘 스크립트 정적 분석 | 1번 검사 불가 |
| ruby | Vagrantfile 문법 검사 | 3번 검사 불가 |
| yq (mikefarah v4) | YAML 파싱 검증 | 4번 검사 불가 |
| kubeconform | K8s 매니페스트 스키마 검증 | 5번 검사 SKIP (CI가 대신 잡아줌) |
| helm | GitOps 차트 렌더링 검증 | 8번 검사 SKIP (CI 밖 추가 검사) |

## 검사 항목과 명령

아래 명령은 CI와 동일하다 (8번만 CI에 없는 로컬 추가 검사).

### 1. ShellCheck — 쉘 스크립트 정적 분석

```bash
find scripts/ -name '*.sh' -print0 | xargs -0 shellcheck --severity=warning
```

`--severity=warning`이라 info/style은 통과한다. 출력이 없으면 PASS.

### 2. 2-space 들여쓰기 검사

CLAUDE.md가 강제하는 쉘 2칸 들여쓰기 규칙. shellcheck이 잡아주지 않아 별도 검사한다.

```bash
bad=0
while IFS= read -r f; do
  width=$(grep -o '^ \+' "$f" | awk '{print length}' | sort -n | head -1)
  if [ -n "$width" ] && [ "$width" -ne 2 ]; then
    echo "$f: smallest indent is $width spaces, expected 2"
    bad=1
  fi
done < <(find scripts/ -name '*.sh')
exit "$bad"   # 셸에 직접 붙여넣을 땐 exit 대신 echo "bad=$bad"
```

### 3. Vagrantfile 문법

```bash
ruby -c Vagrantfile   # "Syntax OK" 가 나와야 PASS
```

### 4. GitOps YAML 유효성 (yq)

```bash
for f in gitops/apps/*.yaml gitops/resources/*.yaml; do
  echo "Validating $f..."
  yq eval '.' "$f" > /dev/null
done
```

### 5. kubeconform — K8s 스키마 검증

CRD 리소스는 datree CRD 카탈로그 스키마로, 스키마가 없는 것은 무시(`-ignore-missing-schemas`)한다.

```bash
kubeconform \
  -strict -summary \
  -kubernetes-version 1.31.0 \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  -ignore-missing-schemas \
  gitops/resources/*.yaml
```

### 6. 클린 인스톨 정적 회귀 검사

과거 클린 설치에서 나온 결함들( [`lessons-log.md`](../common/lessons-log.md)의 날짜 붙은 행들)이
재발하지 않았는지 확인한다. 실패 메시지가 "무엇이 되돌아왔는지"를 지목한다.

```bash
./scripts/test/regression-check-kakao.sh --static
```

`--static`은 클러스터 없이 도는 절반이다. 나머지 절반(런타임)은 2단계에서 클러스터를 띄운 뒤 돈다.

### 7. Mistakes Log 형식 검사

`lessons-log.md`의 표에서 이스케이프 안 된 `|`가 열을 추가해 렌더링을 조용히 깨뜨리는 것을 잡는다.

```bash
python3 - <<'EOF'
import re, sys
bad = 0
for n, line in enumerate(open("docs/common/lessons-log.md"), 1):
    line = line.rstrip("\n")
    if not line.startswith("| ") or line.startswith("|---") or line.startswith("| Date"):
        continue
    cells = re.split(r"(?<!\\)\|", line)
    if len(cells) != 5:
        print(f"lessons-log.md:{n}: {len(cells)-2} columns, expected 3")
        bad += 1
sys.exit(1 if bad else 0)
EOF
```

### 8. Helm 차트 렌더링 (CI 외 추가 검사)

GitOps가 chart 기반(`gitops/charts/`)이 된 뒤로는 yq 파싱만으로 부족하다 — 템플릿이
실제로 렌더링되는지 확인한다. 중복 맵 키(last-wins)나 템플릿 오류는 이것만 잡는다.

```bash
helm template gitops/charts/narwhal-apps \
  --set baseDomain=local.narwhal.internal \
  --set repoURL=http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git \
  --set provider=vagrant > /dev/null
helm template gitops/charts/narwhal-platform > /dev/null
```

> 참고: CI의 Markdown Lint는 `|| true`로 non-blocking이라 0단계 필수 항목에 넣지 않는다.

## 실행 결과 기록

실행 결과 상세는 `work/evaluation/`(git 미추적, 로컬 실행 로그)에 남긴다.
최근 실행 요약을 아래 표에 갱신한다.

### 2026-08-18 (macOS 15.7 arm64 / shellcheck 0.11.0, yq 4.53.3, kubeconform 0.8.0, helm 4.0.5)

| # | 항목 | 결과 | 비고 |
|---|------|------|------|
| 1 | shellcheck (scripts/ 전체) | PASS | 67개 파일, 0 이슈 |
| 2 | 2-space 들여쓰기 | PASS | 위반 0건 |
| 3 | `ruby -c Vagrantfile` | PASS | Syntax OK |
| 4 | GitOps YAML (yq) | PASS | 22개 파일 유효 |
| 5 | kubeconform | PASS | 53/53 리소스 유효 (K8s 1.31.0 스키마) |
| 6 | 정적 회귀 검사 | PASS | 33 passed / 0 failed / 1 warn (R19: 로컬에 airgap 번들 없음 — 정상) |
| 7 | Mistakes Log 형식 | PASS | 위반 0건 |
| 8 | helm template (apps/platform) | PASS | 34 / 57 리소스 렌더링 정상 |

> 로컬 도구는 Homebrew(macOS/arm64) 최신판이라 CI(ubuntu, apt/latest 바이너리)와
> 버전이 완전히 같지는 않다. 검사 로직은 동일하므로 결과 해석에는 지장 없다.

## 트러블슈팅

| 증상 | 원인/해결 |
|------|-----------|
| shellcheck SC2312 등 대량 경고 | `--severity=warning` 없이 실행한 경우. CI와 동일 옵션 사용 |
| yq 오류 `unknown command "eval"` | Python yq가 설치됨. mikefarah yq v4 필요 (`brew install yq`) |
| kubeconform 스키마 다운로드 실패 | 프록시/방화벽 환경. CRD 카탈로그는 GitHub raw 접근 필요 |
| 들여쓰기 검사에서 특정 파일 실패 | 최소 들여쓰기가 2가 아님 — 탭 또는 4칸 혼입. 해당 파일 수정 |
| regression-check 실패 | 메시지가 lessons-log의 어느 행이 재발했는지 알려준다. 해당 행 참조 |
