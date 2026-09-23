#!/usr/bin/env python3
import argparse, fnmatch, json, re, sys
from pathlib import Path
RISK_ORDER={"low":0,"medium":1,"high":2}
SHA40_RE=re.compile(r"^[0-9a-f]{40}$")
# Characters str.splitlines() treats as line breaks but a naive "\n".split() (and most
# YAML/diff tooling) does not: \r alone, \x85 (NEL),  /  (Unicode line/paragraph
# separators), plus the rarer \v/\f/\x1c-\x1e. A line smuggling one of these can look like a
# single harmless `uses:` line to a splitlines()-based scanner while a YAML parser (or a
# terminal, or git) renders it as two lines -- one of which can be an arbitrary `run:` step.
FORBIDDEN_LINE_CHARS=("\r","\x85"," "," ","\v","\f","\x1c","\x1d","\x1e")
DIFF_HEADER_RE=re.compile(r"^diff --git a/(?P<a>.+) b/(?P<b>.+)$")
DISALLOWED_DIFF_MARKERS=("Binary files ","GIT binary patch","rename from ","rename to ","copy from ","copy to ","old mode ","new mode ","deleted file mode ","new file mode ")
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
def load_text(path):
  if not path: return None
  p=Path(path)
  if not p.exists(): return None
  # Path.read_text() opens in universal-newlines text mode, which silently translates a
  # bare \r (and \r\n) to \n *before* this script ever sees it -- exactly the line-break
  # character this file is trying to detect and reject. Read raw bytes and decode without
  # newline translation so a smuggled \r/\x85/ /  survives to the forbidden-char
  # check below instead of being normalized away first.
  return p.read_bytes().decode("utf-8",errors="replace")
def is_workflow_file(path,patterns):
  # A dedicated slash-count guard, not just fnmatch: fnmatch's `*` matches `/` too, so
  # ".github/workflows/*.yml" alone would also match a nested ".github/workflows/x/y.yml".
  # GitHub only ever runs the flat, top-level files, so require exactly that shape.
  if path.count("/")!=2 or not path.startswith(".github/workflows/"): return False
  return any(fnmatch.fnmatchcase(path,pat) for pat in patterns)
def parse_workflow_diff(diff_text):
  files={}; current=None
  for raw in diff_text.split("\n"):
    m=DIFF_HEADER_RE.match(raw)
    if m: current=m.group("b"); files[current]={"content":[],"disallowed":[]}; continue
    if current is None: continue
    if raw.startswith("+++ ") or raw.startswith("--- "): continue
    if any(raw.startswith(marker) for marker in DISALLOWED_DIFF_MARKERS):
      files[current]["disallowed"].append(raw); continue
    if raw[:1] in ("+","-"): files[current]["content"].append(raw)
  return files
def validate_pin_only_diff(diff_text,touched_workflows,pin_only_re):
  bad_chars=[c for c in FORBIDDEN_LINE_CHARS if c in diff_text]
  if bad_chars:
    return False,"workflow diff contains a disallowed line-break character: "+", ".join(repr(c) for c in bad_chars)
  files=parse_workflow_diff(diff_text)
  for wf in touched_workflows:
    block=files.get(wf)
    if block is None: return False,f"no parseable diff hunk found for touched workflow file {wf}"
    if block["disallowed"]:
      return False,f"{wf}: disallowed diff marker (binary/rename/copy/mode change): {block['disallowed'][0]!r}"
    content=block["content"]
    if not content: return False,f"{wf}: no content change lines in diff (empty, mode-only, or unreadable diff)"
    removed=[]; added=[]; offending=[]
    for line in content:
      sign,body=line[0],line[1:]
      m=pin_only_re.match(body)
      ref=m.group("ref") if m else None
      if not m or "@" not in ref: offending.append(line); continue
      key,sha=ref.rsplit("@",1)
      (removed if sign=="-" else added).append((key,sha,line))
    if offending:
      return False,f"{wf}: non-`uses:`-pin change(s): "+"; ".join(offending[:5])
    if len(removed)!=len(added):
      return False,f"{wf}: unpaired uses: change(s) ({len(removed)} removed vs {len(added)} added)"
    remaining=list(added)
    for key,old_sha,old_line in removed:
      idx=next((i for i,(k,_,_) in enumerate(remaining) if k==key),None)
      if idx is None:
        return False,f"{wf}: unpaired uses: change for {key!r} (no matching add with the identical owner/repo(/path))"
      new_key,new_sha,new_line=remaining.pop(idx)
      if not SHA40_RE.fullmatch(new_sha):
        return False,f"{wf}: new ref for {key!r} is not a 40-character commit SHA: {new_sha!r}"
  return True,None
def commit_provenance_trusted(commit_authors_text,trusted):
  if commit_authors_text is None: return False,"commit author list unavailable; cannot verify commit provenance"
  lines=[ln.strip() for ln in commit_authors_text.split("\n") if ln.strip()]
  if not lines: return False,"commit author list is empty; cannot verify commit provenance"
  for ln in lines:
    parts=ln.split()
    if len(parts)!=2:
      return False,f"malformed commit-author entry: {ln!r}"
    author_login,committer_login=parts
    if author_login not in trusted or committer_login not in trusted:
      return False,f"commit not authored and committed by a trusted login: {ln!r}"
  return True,None
def dependency_bump_exemption(changed,policy,pr_author,workflow_diff_text,commit_authors_text):
  # Narrow, fail-closed carve-out for Dependabot pin bumps: the requirement above (a NEW
  # trace changed in every high-risk PR) can never be satisfied by Dependabot, which only
  # ever edits `uses:` pins and cannot author a trace file. Any condition below that cannot
  # be verified is treated as "not exempt". Threat model includes a write-access insider
  # pushing extra commits onto an otherwise-legitimate Dependabot branch/PR.
  cfg=policy.get("dependencyBumpExemption")
  if not isinstance(cfg,dict): return False,"no dependencyBumpExemption configured"
  trusted=set(cfg.get("trustedAuthors",[]))
  if not pr_author or pr_author not in trusted:
    return False,f"author {pr_author!r} is not a trusted dependency-bump author"
  ok,detail=commit_provenance_trusted(commit_authors_text,trusted)
  if not ok: return False,detail
  allowed=cfg.get("allowedPathPatterns",[])
  if not allowed: return False,"no allowedPathPatterns configured"
  disallowed=[f for f in changed if not is_workflow_file(f,allowed)]
  if disallowed:
    return False,"changed file(s) outside dependency-bump allowlist: "+", ".join(disallowed)
  touched_workflows=[f for f in changed if is_workflow_file(f,allowed)]
  if touched_workflows:
    if workflow_diff_text is None:
      return False,"workflow diff unavailable; cannot verify pin-only change"
    pin_re=re.compile(cfg.get("pinOnlyLineRe",r"^\s*-?\s*uses:\s*(?P<ref>\S+)(?:\s*#.*)?$"))
    ok,detail=validate_pin_only_diff(workflow_diff_text,touched_workflows,pin_re)
    if not ok: return False,detail
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
  p.add_argument("--commit-authors",help="path to a file with one '<author login> <committer login>' pair per PR commit, used only to evaluate the dependency-bump exemption")
  a=p.parse_args()
  try:
    policy=load_policy(a.policy); changed=[x.strip() for x in Path(a.changed_files).read_text().splitlines() if x.strip()]
    risk,matches=classify(changed,policy); prefix=policy.get("tracePathPrefix",".agents/evals/traces/")
    trace=any(x.startswith(prefix) and x.endswith(".json") for x in changed); required=risk in set(policy.get("traceRequiredAt",["high"]))
    exempt=False; exemption_detail=None
    if required and not trace:
      exempt,exemption_detail=dependency_bump_exemption(changed,policy,a.pr_author,load_text(a.workflow_diff),load_text(a.commit_authors))
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
