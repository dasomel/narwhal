#!/usr/bin/env python3
import os
import json
import re
import argparse
import subprocess
import math
import tempfile

def load_jsonl(path):
    results = []
    if not os.path.exists(path):
        return results
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results

def wilson_score_interval(successes, total, confidence=0.95):
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    z = 1.96  # 95% confidence
    denominator = 1 + (z**2) / total
    center = (p + (z**2) / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p)) / total + (z**2) / (4 * total**2)) / denominator
    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return lower, upper

def main():
    parser = argparse.ArgumentParser(description="Evaluate Velero logs RTK Gates G1-G4.")
    parser.add_argument("--raw", required=True, help="Path to raw arm output JSONL")
    parser.add_argument("--rtk", required=True, help="Path to rtk arm output JSONL")
    parser.add_argument("--corpus-dir", required=True, help="Path to corpus directory")
    args = parser.parse_args()

    # Load results
    raw_results = {item["id"]: item for item in load_jsonl(args.raw)}
    rtk_results = {item["id"]: item for item in load_jsonl(args.rtk)}

    # Load labels
    labels = {}
    for filename in os.listdir(args.corpus_dir):
        if filename.endswith(".label.json"):
            path = os.path.join(args.corpus_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                label = json.load(f)
                labels[label["id"]] = label

    # Verify matching samples
    sample_ids = sorted(list(labels.keys()))
    N = len(sample_ids)

    # G1: Evidence Preservation
    g1_passed = True
    g1_results = {}
    for sid in sample_ids:
        label = labels[sid]
        if label.get("severity") == "CRITICAL":
            log_path = os.path.join(args.corpus_dir, f"{sid}.log")
            # Run rtk-wrap.sh to get the delivered output (re-running COMPRESSOR)
            res = subprocess.run(["./compressor/rtk-wrap.sh", log_path], capture_output=True, text=True)
            if res.returncode != 0:
                g1_passed = False
                g1_results[sid] = False
                continue
            delivered = res.stdout
            matched = True
            for pattern in label.get("critical_evidence", []):
                if not re.search(pattern, delivered):
                    matched = False
                    break
            g1_results[sid] = matched
            if not matched:
                g1_passed = False

    # G2: Accuracy
    raw_correct = 0
    rtk_correct = 0
    
    for sid in sample_ids:
        label = labels[sid]
        expected_phase = label.get("expected_phase", "")
        expected_rc = label.get("root_cause", "")

        # Check raw
        raw_res = raw_results.get(sid, {})
        raw_phase = raw_res.get("answer_phase", "")
        raw_rc = raw_res.get("answer_root_cause", "")
        
        raw_phase_ok = raw_phase.strip().lower() == expected_phase.strip().lower()
        if expected_phase.strip().lower() == "completed":
            raw_rc_ok = True
        else:
            raw_rc_ok = expected_rc.strip().lower() in raw_rc.strip().lower() if expected_rc else True
        
        if raw_phase_ok and raw_rc_ok:
            raw_correct += 1

        # Check rtk
        rtk_res = rtk_results.get(sid, {})
        rtk_phase = rtk_res.get("answer_phase", "")
        rtk_rc = rtk_res.get("answer_root_cause", "")
        
        rtk_phase_ok = rtk_phase.strip().lower() == expected_phase.strip().lower()
        if expected_phase.strip().lower() == "completed":
            rtk_rc_ok = True
        else:
            rtk_rc_ok = expected_rc.strip().lower() in rtk_rc.strip().lower() if expected_rc else True
            
        if rtk_phase_ok and rtk_rc_ok:
            rtk_correct += 1

    sr_raw = raw_correct / N if N > 0 else 0.0
    sr_rtk = rtk_correct / N if N > 0 else 0.0
    
    g2_passed = sr_rtk >= (sr_raw - 0.02)

    # G3: CPCA
    raw_total_tokens = sum(item.get("input_tokens", 0) + item.get("output_tokens", 0) for item in raw_results.values())
    rtk_total_tokens = sum(item.get("input_tokens", 0) + item.get("output_tokens", 0) for item in rtk_results.values())

    cpca_raw = raw_total_tokens / raw_correct if raw_correct > 0 else float("inf")
    cpca_rtk = rtk_total_tokens / rtk_correct if rtk_correct > 0 else float("inf")

    g3_passed = cpca_rtk <= 0.8 * cpca_raw if cpca_raw != float("inf") else False

    # G4: Fallback
    g4_passed = True
    # Test fallback G4 via temporary files
    try:
        # Test 1: empty file
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            temp_path = f.name
        try:
            res = subprocess.run(["./compressor/rtk-wrap.sh", temp_path], capture_output=True, text=True)
            fallback_line = [l for l in res.stderr.splitlines() if "RTK_FALLBACK=" in l]
            is_fallback = fallback_line and fallback_line[0] == "RTK_FALLBACK=1"
            if not is_fallback or res.stdout != "":
                g4_passed = False
        finally:
            os.remove(temp_path)

        # Test 2: compressor fails (COMPRESSOR=false)
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            f.write("test content\n")
            temp_path = f.name
        try:
            env = os.environ.copy()
            env["COMPRESSOR"] = "false"
            res = subprocess.run(["./compressor/rtk-wrap.sh", temp_path], env=env, capture_output=True, text=True)
            fallback_line = [l for l in res.stderr.splitlines() if "RTK_FALLBACK=" in l]
            is_fallback = fallback_line and fallback_line[0] == "RTK_FALLBACK=1"
            if not is_fallback or res.stdout != "test content\n":
                g4_passed = False
        finally:
            os.remove(temp_path)

        # Test 3: evidence drops (COMPRESSOR="grep -v error")
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            f.write("level=error msg=\"fatal issue\"\nnormal line\n")
            temp_path = f.name
        try:
            env = os.environ.copy()
            env["COMPRESSOR"] = "grep -v error"
            res = subprocess.run(["./compressor/rtk-wrap.sh", temp_path], env=env, capture_output=True, text=True)
            fallback_line = [l for l in res.stderr.splitlines() if "RTK_FALLBACK=" in l]
            is_fallback = fallback_line and fallback_line[0] == "RTK_FALLBACK=1"
            if not is_fallback or res.stdout != "level=error msg=\"fatal issue\"\nnormal line\n":
                g4_passed = False
        finally:
            os.remove(temp_path)

    except Exception as e:
        print(f"Error during G4 fallback check execution: {e}")
        g4_passed = False

    # Wilson interval calculation
    raw_ci_lower, raw_ci_upper = wilson_score_interval(raw_correct, N)
    rtk_ci_lower, rtk_ci_upper = wilson_score_interval(rtk_correct, N)

    # Decision
    adopt = g1_passed and g2_passed and g3_passed and g4_passed
    decision = "ADOPT" if adopt else "REJECT"

    # Display warning if N < 20
    if N < 20:
        print(f"\n⚠️  통계적 신뢰 부족 (N={N} < 20)")

    # Print results table
    print("\n============================================================")
    print("                RTK GATES EVALUATION REPORT")
    print("============================================================")
    print(f"G1 [Evidence Preservation] : {'PASS' if g1_passed else 'FAIL'}")
    print(f"G2 [Accuracy]              : {'PASS' if g2_passed else 'FAIL'}")
    print(f"   - Raw Success Rate: {sr_raw:.2%} (95% CI: [{raw_ci_lower:.2%}, {raw_ci_upper:.2%}])")
    print(f"   - RTK Success Rate: {sr_rtk:.2%} (95% CI: [{rtk_ci_lower:.2%}, {rtk_ci_upper:.2%}])")
    print(f"G3 [CPCA Token cost]       : {'PASS' if g3_passed else 'FAIL'}")
    print(f"   - CPCA Raw: {cpca_raw:.1f} tokens/correct")
    ratio_str = f"{cpca_rtk/cpca_raw:.2%}" if cpca_raw != float("inf") else "N/A"
    print(f"   - CPCA RTK: {cpca_rtk:.1f} tokens/correct (Ratio: {ratio_str})")
    print(f"G4 [Fallback Mechanism]    : {'PASS' if g4_passed else 'FAIL'}")
    print("------------------------------------------------------------")
    print(f"DECISION: {decision}")
    print("============================================================\n")

if __name__ == "__main__":
    main()
