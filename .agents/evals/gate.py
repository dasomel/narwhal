#!/usr/bin/env python3
import argparse, json, subprocess, sys
from pathlib import Path

ORDER = {"false":0,"na":1,"true":2}


def load(path):
    return json.loads(Path(path).read_text())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace", required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--current-out", default="/tmp/current-agent-eval.json")
    p.add_argument("--comparison-out", default="/tmp/agent-eval-comparison.json")
    a = p.parse_args()

    evaluator = Path(__file__).with_name("evaluate.py")
    rc = subprocess.run([sys.executable, str(evaluator), a.trace, "--out", a.current_out]).returncode
    if rc == 2:
        return 2
    baseline, current = load(a.baseline), load(a.current_out)
    before = {r["behavior"]:r["outcome"] for r in baseline.get("results",[])}
    after = {r["behavior"]:r["outcome"] for r in current.get("results",[])}
    regressions, changes = [], []
    for behavior in sorted(set(before)|set(after)):
        old, new = before.get(behavior,"na"), after.get(behavior,"na")
        if old == new: continue
        item = {"behavior":behavior,"from":old,"to":new}
        changes.append(item)
        if ORDER.get(new,-1) < ORDER.get(old,-1): regressions.append(item)
    report = {"schemaVersion":"openforge-agent-eval-comparison/v1","baselineTraceId":baseline.get("traceId"),"currentTraceId":current.get("traceId"),"summary":{"regressions":len(regressions),"changed":len(changes)},"regressions":regressions,"changes":changes}
    Path(a.comparison_out).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
