#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path

TRACE_SCHEMA = "openforge-agent-trace/v1"
EVAL_SCHEMA = "openforge-agent-eval/v1"


def load_trace(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("trace root must be an object")
    return data


def events(trace, kind):
    return [e for e in trace.get("events", []) if e.get("type") == kind]


def result(behavior, outcome, evidence, reason):
    return {"behavior": behavior, "outcome": outcome, "evidence": evidence, "reason": reason}


def evaluate(trace):
    if trace.get("schemaVersion") != TRACE_SCHEMA:
        raise ValueError(f"schemaVersion must be {TRACE_SCHEMA}")
    if not trace.get("traceId") or not isinstance(trace.get("events"), list):
        raise ValueError("traceId and events are required")
    ids = [e.get("id") for e in trace["events"]]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("event ids must be present and unique")

    claims = events(trace, "completion_claim")
    checks = [e for e in events(trace, "verification") if e.get("scope") and e.get("evidence")]
    evidence_result = result(
        "evidence-before-claim",
        "na" if not claims else ("true" if checks else "false"),
        [e["id"] for e in checks or claims],
        "Scoped verification evidence recorded" if checks else ("No completion claim recorded" if not claims else "Completion claim lacks scoped verification evidence"),
    )

    bad_scope = events(trace, "unrelated_change") + [e for e in events(trace, "scope_expansion") if not e.get("approved", False)]
    scoped = events(trace, "scope_check") + events(trace, "change") + events(trace, "bug_fix")
    scope_result = result(
        "scope-discipline",
        "false" if bad_scope else ("true" if scoped else "na"),
        [e["id"] for e in (bad_scope or scoped[:5])],
        "Unrelated or unapproved scope expansion recorded" if bad_scope else ("No scope violation recorded" if scoped else "No scoped change evidence recorded"),
    )

    fixes, repro, regression = events(trace, "bug_fix"), events(trace, "reproduction"), events(trace, "regression_verification")
    bug_result = result(
        "bug-fix-verification",
        "na" if not fixes else ("true" if repro and regression else "false"),
        [e["id"] for e in (repro[-1:] + regression[-1:])],
        "No bug fix recorded" if not fixes else ("Reproduction and regression verification recorded" if repro and regression else "Bug fix lacks reproduction or regression verification"),
    )

    outcomes = events(trace, "task_outcome")
    if not outcomes:
        convergence_result = result("task-convergence", "false", [], "No task outcome recorded")
    else:
        last = outcomes[-1]
        state = str(last.get("state", "")).upper()
        ok = state in {"A", "B", "C"} and (state == "A" or bool(last.get("next")))
        convergence_result = result("task-convergence", "true" if ok else "false", [last["id"]], f"Convergence state {state} recorded" if ok else "Invalid or incomplete convergence outcome")

    inputs = events(trace, "external_input")
    bad_inputs = [e for e in inputs if not e.get("provenance") or not e.get("reviewed", False)]
    trust_result = result(
        "trust-and-provenance",
        "na" if not inputs else ("false" if bad_inputs else "true"),
        [e["id"] for e in (bad_inputs or inputs)],
        "No external behavior, skill, or spec input recorded" if not inputs else ("External input lacks provenance or review" if bad_inputs else "External input provenance and review recorded"),
    )

    results = [evidence_result, scope_result, bug_result, convergence_result, trust_result]
    applicable = [r for r in results if r["outcome"] != "na"]
    passed = sum(r["outcome"] == "true" for r in applicable)
    failed = sum(r["outcome"] == "false" for r in applicable)
    return {"schemaVersion": EVAL_SCHEMA, "traceId": trace["traceId"], "summary": {"passed": passed, "failed": failed, "notApplicable": len(results)-len(applicable), "scorePercent": round(passed/len(applicable)*100,1) if applicable else None}, "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        data = evaluate(load_trace(args.trace))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(data, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 1 if data["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
