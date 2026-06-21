# Velero Logs RTK Evaluation Report

> [!IMPORTANT]
> **이 PoC 미통과 시 정책 D1대로 RTK 비활성화 유지**
> (As per [rtk-token-compression-policy.md](../../docs/rtk-token-compression-policy.md), RTK remains disabled by default unless all Gates G1-G4 pass.)

## 1. 데이터셋 메타데이터 (Dataset Metadata)

| 항목 | 값 / 사양 |
| :--- | :--- |
| **Velero Target Version** | `v1.12.0` (Fixed) |
| **Corpus Size (N)** | 3 sample pairs (001-success, 002-partially-failed, 003-plugin-panic) |
| **Evaluated Date** | 2026-06-22 |
| **Evaluation Method** | STUB / Heuristic parser wiring validation |

---

## 2. 사전등록 게이트 및 결과 (Pre-registered Gates & Results)

| 게이트 | 정의 | 통과 기준 (Threshold) | 결과 (PASS/FAIL) | 상세 수치 |
| :--- | :--- | :--- | :---: | :--- |
| **G1** | **증거 보존 (Evidence Preservation)** | Critical 로그의 에러/패닉 증거(정규식) 100% 보존 | **[ ]** | - Critical 샘플 수: 2<br>- 보존 성공률: `___%` |
| **G2** | **진단 정확도 (Accuracy)** | $SR_{rtk} \ge SR_{raw} - 0.02$ | **[ ]** | - Raw SR: `___%`<br>- RTK SR: `___%` |
| **G3** | **비용 대비 성능 (CPCA)** | $CPCA_{rtk} \le 0.8 \times CPCA_{raw}$ | **[ ]** | - Raw CPCA: `___` tokens/correct<br>- RTK CPCA: `___` tokens/correct<br>- 비용 비율: `___%` |
| **G4** | **폴백 안전장치 (Fallback)** | 빈 파일 / 에러 누락 / 압축기 실패 시 원본 폴백 작동 및 `RTK_FALLBACK=1` 출력 | **[ ]** | - 빈 입력 검증: [ ] PASS / [ ] FAIL<br>- 프로세스 실패 검증: [ ] PASS / [ ] FAIL<br>- 마커 누락 검증: [ ] PASS / [ ] FAIL |

### 최종 결정 (Final Decision)
* **[ ] ADOPT** (G1, G2, G3, G4 모두 PASS인 경우)
* **[ ] REJECT** (하나라도 FAIL인 경우)

---

## 3. 한계점 및 개선 방향 (Limitations & Future Work)

1. **Corpus 현실성 (Corpus Realism)**:
   - 현재 코퍼스는 각 20~30라인 내외의 초소형 합성 로그이므로 실제 대용량 Velero 백업 로그(수천~만 라인)에서의 노이즈 및 토큰 압축률을 완벽히 모사하지 못함.
2. **포맷 드리프트 (Format Drift)**:
   - Velero 버전 업그레이드에 따라 로그 포맷 및 오류 출력 형태가 바뀔 경우 기존에 설정한 `EVIDENCE_RE` 증거 마커 정규식의 미스매칭이 일어날 가능성이 존재함.
3. **평가 편향 (Judge Bias)**:
   - 본 PoC는 키워드/정규식 기반의 채점 방식을 사용하므로 실제 LLM-judge 및 사람의 스팟 체크를 통한 진단 수준 검증보다 단순할 수 있음.
