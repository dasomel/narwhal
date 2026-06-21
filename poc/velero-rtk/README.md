# Velero Logs RTK Evaluation PoC Harness

본 디렉토리는 Velero logs에 RTK(Run-Time token-compression: 터미널 출력 정규식/필터 압축) 방식을 도입하는 것이 타당한지 정량 판정하기 위한 평가 하네스(Harness)입니다. 정책 문서 [rtk-token-compression-policy.md](../../docs/rtk-token-compression-policy.md) §4 Escape Hatch에서 규정하는 4대 게이트(G1~G4)를 평가하여 최종 도입 여부(`ADOPT`/`REJECT`)를 결정합니다.

## 디렉토리 구조 (Directory Structure)

* **`compressor/`**: 압축 메커니즘을 정의하는 블랙박스 스크립트 모음.
  * [`rtk-wrap.sh`](file:///Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal/poc/velero-rtk/compressor/rtk-wrap.sh): 압축 후 증거 누락 시 원본 복구를 수행하는 폴백 계약 래퍼.
  * [`dummy-filter.sh`](file:///Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal/poc/velero-rtk/compressor/dummy-filter.sh): 플레이스홀더 더미 압축기 (INFO/DEBUG 로그만 라인 단위로 필터링).
* **`corpus/`**: 실제 환경과 유사하게 재구성한 백업/복구 로그 샘플 및 그에 따른 질문의 기대 정답 라벨.
  * `001-success.log` / `001-success.label.json`
  * `002-partially-failed.log` / `002-partially-failed.label.json`
  * `003-plugin-panic.log` / `003-plugin-panic.label.json`
* **[`questions.json`](file:///Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal/poc/velero-rtk/questions.json)**: LLM 에이전트에게 물어볼 구조화된 질문 셋.
* **[`run-arm.sh`](file:///Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal/poc/velero-rtk/run-arm.sh)**: 개별 평가 암(raw 또는 rtk)을 실행하여 모델 입력/출력 및 토큰 수를 기록하는 하네스 실행기.
* **[`score.py`](file:///Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal/poc/velero-rtk/score.py)**: 출력 결과를 바탕으로 G1~G4 게이트를 평가하여 최종 판정(DECISION)을 내리는 채점 스크립트.
* **[`REPORT.md`](file:///Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal/poc/velero-rtk/REPORT.md)**: 게이트 평가 기준 및 최종 결과 기록 문서.

---

## 실행 방법 (Usage)

### 1. 배선 및 골격 검증 (Wiring Validation via STUB)
기본 내장된 STUB 에이전트와 더미 필터를 사용하여 하네스 전체의 배선이 정상인지 검증합니다.
```bash
# 1. raw 로그에 대한 에이전트 응답 수집
./run-arm.sh raw out-raw.jsonl

# 2. rtk 압축 로그에 대한 에이전트 응답 수집
./run-arm.sh rtk out-rtk.jsonl

# 3. G1~G4 게이트 및 신뢰구간 평가 실행
python3 score.py --raw out-raw.jsonl --rtk out-rtk.jsonl --corpus-dir corpus/
```

### 2. 실제 압축기 및 LLM 플러깅 (Real Evaluation)
실제 RTK 압축기와 LLM CLI를 사용하여 평가하려면 환경변수를 주입하여 실행합니다.
```bash
export COMPRESSOR="/path/to/real-rtk-compressor.sh"
export AGENT_CMD="my-llm-cli --model gemini-1.5-pro"

./run-arm.sh raw out-raw.jsonl
./run-arm.sh rtk out-rtk.jsonl
python3 score.py --raw out-raw.jsonl --rtk out-rtk.jsonl --corpus-dir corpus/
```
