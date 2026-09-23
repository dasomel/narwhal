#!/usr/bin/env python3
import argparse, fnmatch, json, re, sys
from pathlib import Path
RISK_ORDER={"low":0,"medium":1,"high":2}
def load_policy(path):
  data=json.loads(Path(path).read_text())
  if data.get("schemaVersion")!="openforge-agent-risk-policy/v1" or not isinstance(data.get("rules"),list): raise ValueError("invalid risk policy")
  return data
def classify(paths,policy):
  highest=policy.get("defaultRisk","low"); matches=[]
  if highest not in RISK_ORDER: raise ValueError("unknown default risk")
  for path in paths:
    for rule in policy["rules"]:
      risk=rule.get("risk"); pattern=rule.get("pattern")
      if risk not in RISK_ORDER or not pattern: raise ValueError("invalid rule")
      if fnmatch.fnmatchcase(path,pattern):
        matches.append({"path":path,"risk":risk,"pattern":pattern,"reason":rule.get("reason","")})
        if RISK_ORDER[risk]>RISK_ORDER[highest]: highest=risk
  return highest,matches
def load_diff_lines(path):
  if not path: return None
  p=Path(path)
  if not p.exists(): return None
  return p.read_text(encoding="utf-8", errors="replace").splitlines()
def dependency_bump_exemption(changed,policy,pr_author,workflow_diff_lines):
  # Narrow, fail-closed carve-out for Dependabot pin bumps: the requirement above
  # (a NEW trace changed in every high-risk PR) can never be satisfied by Dependabot,
  # which only ever edits `uses:` pins / manifests and cannot author a trace file.
  # Any condition below that cannot be verified is treated as "not exempt".
  cfg=policy.get("dependencyBumpExemption")
  if not isinstance(cfg,dict): return False,"no dependencyBumpExemption configured"
  trusted=set(cfg.get("trustedAuthors",[]))
  if not pr_author or pr_author not in trusted:
    return False,f"author {pr_author!r} is not a trusted dependency-bump author"
  allowed=cfg.get("allowedPathPatterns",[])
  if not allowed: return False,"no allowedPathPatterns configured"
  disallowed=[f for f in changed if not any(fnmatch.fnmatchcase(f,pat) for pat in allowed)]
  if disallowed:
    return False,"changed file(s) outside dependency-bump allowlist: "+", ".join(disallowed)
  workflow_patterns=cfg.get("workflowPathPatterns",[])
  touched_workflows=[f for f in changed if any(fnmatch.fnmatchcase(f,pat) for pat in workflow_patterns)]
  if touched_workflows:
    if workflow_diff_lines is None:
      return False,"workflow diff unavailable; cannot verify pin-only change"
    pin_re=re.compile(cfg.get("pinOnlyLineRe",r"^[+-]\s*-?\s*uses:\s*\S+"))
    offending=[]
    for line in workflow_diff_lines:
      if not line or line[0] not in "+-": continue
      if line.startswith("+++") or line.startswith("---"): continue
      if not pin_re.match(line): offending.append(line)
    if offending:
      return False,"workflow diff has non-`uses:` change(s): "+"; ".join(offending[:5])
  standing=cfg.get("standingTrace")
  if not standing or not Path(standing).exists():
    return False,f"standing trace {standing!r} is missing"
  return True,standing
def main():
  p=argparse.ArgumentParser()
  p.add_argument("--policy",required=True)
  p.add_argument("--changed-files",required=True)
  p.add_argument("--report-out")
  p.add_argument("--pr-author",default="",help="PR author login (e.g. github.event.pull_request.user.login), passed via env")
  p.add_argument("--workflow-diff",help="path to a unified diff limited to .github/workflows/**, used only to evaluate the dependency-bump exemption")
  a=p.parse_args()
  try:
    policy=load_policy(a.policy); changed=[x.strip() for x in Path(a.changed_files).read_text().splitlines() if x.strip()]
    risk,matches=classify(changed,policy); prefix=policy.get("tracePathPrefix",".agents/evals/traces/")
    trace=any(x.startswith(prefix) and x.endswith(".json") for x in changed); required=risk in set(policy.get("traceRequiredAt",["high"]))
    exempt=False; exemption_detail=None
    if required and not trace:
      exempt,exemption_detail=dependency_bump_exemption(changed,policy,a.pr_author,load_diff_lines(a.workflow_diff))
    result={"schemaVersion":"openforge-agent-risk-result/v1","risk":risk,"traceRequired":required,"traceChanged":trace,
            "traceExempt":exempt,"traceExemptionDetail":exemption_detail,"changedFiles":changed,"matches":matches}
    if a.report_out: Path(a.report_out).write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
    if required and not trace and not exempt:
      print(f"High-risk change requires an operational trace change under {prefix}",file=sys.stderr)
      if exemption_detail: print(f"dependency-bump exemption not applicable: {exemption_detail}",file=sys.stderr)
      return 1
    return 0
  except (OSError,json.JSONDecodeError,ValueError) as e:
    print(f"risk policy error: {e}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
