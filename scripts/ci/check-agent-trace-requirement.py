#!/usr/bin/env python3
import argparse, fnmatch, json, re, sys
from pathlib import Path
RISK_ORDER={"low":0,"medium":1,"high":2}
SHA40_RE=re.compile(r"^[0-9a-f]{40}$")
# Characters str.splitlines() treats as line breaks but a naive "\n".split() (and most
# YAML/diff tooling) does not: \r alone, \x85 (NEL), U+2028/U+2029 (Unicode line/paragraph
# separators), plus the rarer \v/\f/\x1c-\x1e. A line smuggling one of these can look like a
# single harmless `uses:` line to a splitlines()-based scanner while a YAML parser (or a
# terminal, or git) renders it as two lines -- one of which can be an arbitrary `run:` step.
FORBIDDEN_LINE_CHARS=("\r","\x85"," "," ","\v","\f","\x1c","\x1d","\x1e")
DIFF_HEADER_RE=re.compile(r"^diff --git a/(?P<a>.+) b/(?P<b>.+)$")
HUNK_HEADER_RE=re.compile(r"^@@ .*@@")
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
  # Hunks are tracked (not just a flat per-file line list) so pairing below can require a
  # removed/added `uses:` line to belong to the *same* hunk -- a change at one call site
  # should not be allowed to "pair" with an unrelated change at a different call site just
  # because both touch the same action.
  files={}; current=None; hunk=None
  for raw in diff_text.split("\n"):
    m=DIFF_HEADER_RE.match(raw)
    if m: current=m.group("b"); files[current]={"hunks":[],"disallowed":[]}; hunk=None; continue
    if current is None: continue
    if raw.startswith("+++ ") or raw.startswith("--- "): continue
    if any(raw.startswith(marker) for marker in DISALLOWED_DIFF_MARKERS):
      files[current]["disallowed"].append(raw); continue
    if HUNK_HEADER_RE.match(raw):
      hunk=[]; files[current]["hunks"].append(hunk); continue
    if raw[:1] in ("+","-") and hunk is not None: hunk.append(raw)
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
    hunks=block["hunks"]
    if not hunks or not any(hunks):
      return False,f"{wf}: no content change lines in diff (empty, mode-only, or unreadable diff)"
    for hunk in hunks:
      if not hunk: continue
      removed=[]; added=[]; offending=[]
      for line in hunk:
        sign,body=line[0],line[1:]
        m=pin_only_re.match(body)
        ref=m.group("ref") if m else None
        prefix=m.group("prefix") if m else None
        if not m or "@" not in ref: offending.append(line); continue
        key,sha=ref.rsplit("@",1)
        (removed if sign=="-" else added).append((prefix,key,sha,line))
      if offending:
        return False,f"{wf}: non-`uses:`-pin change(s): "+"; ".join(offending[:5])
      if len(removed)!=len(added):
        return False,f"{wf}: unpaired uses: change(s) in one hunk ({len(removed)} removed vs {len(added)} added)"
      remaining=list(added)
      for prefix,key,old_sha,old_line in removed:
        # Pairing requires an *identical* prefix (indentation/list-marker text before
        # `uses:`) as well as the identical owner/repo(/path): otherwise a line that merely
        # moved, re-indented, or was replaced by a differently-positioned step would still
        # "pair" on owner/repo alone.
        idx=next((i for i,(p,k,_,_) in enumerate(remaining) if p==prefix and k==key),None)
        if idx is None:
          return False,f"{wf}: unpaired uses: change for {key!r} (no matching add with identical indentation and owner/repo(/path) in the same hunk)"
        _,_,new_sha,new_line=remaining.pop(idx)
        if not SHA40_RE.fullmatch(new_sha):
          return False,f"{wf}: new ref for {key!r} is not a 40-character commit SHA: {new_sha!r}"
  return True,None
def parse_commit_records(text):
  records=[]
  for ln in text.split("\n"):
    ln=ln.strip()
    if not ln: continue
    try: records.append(json.loads(ln))
    except json.JSONDecodeError: return None
  return records
def commit_provenance_trusted(commit_records_text,expected_count,trusted_author_login,trusted_committer_login):
  # `.author.login`/`.committer.login` are GitHub matching a commit's author/committer EMAIL
  # to an account -- an insider can set `git commit --author="dependabot[bot] <...noreply...>"`
  # and a matching committer email and get the same logins with no GitHub involvement at all.
  # The only part of this that is actually authenticated is `commit.verification`: GitHub sets
  # verified=true/reason="valid" only for commits it itself signed (which is how every commit
  # authored via the Dependabot API and committed through GitHub's own "web-flow" identity is
  # produced). Login match alone is necessary but not sufficient; verification is what makes it
  # trustworthy.
  if commit_records_text is None:
    return False,"commit record list unavailable; cannot verify commit provenance"
  records=parse_commit_records(commit_records_text)
  if records is None:
    return False,"commit record list is not valid JSON-lines"
  if not records:
    return False,"commit record list is empty; cannot verify commit provenance"
  if expected_count is None:
    return False,"expected PR commit count not provided; cannot verify the fetched commit list is complete"
  if len(records)!=expected_count:
    return False,(f"fetched commit count ({len(records)}) does not match the PR's reported commit count "
                   f"({expected_count}); the pulls/commits API caps at 250 results even with pagination, "
                   "so a mismatch means some commits were never checked")
  for rec in records:
    sha=rec.get("sha","?"); author_login=rec.get("author_login"); committer_login=rec.get("committer_login")
    verified=rec.get("verified"); reason=rec.get("reason")
    if author_login!=trusted_author_login or committer_login!=trusted_committer_login:
      return False,(f"commit {sha!r} is not authored by {trusted_author_login!r} and committed by "
                     f"{trusted_committer_login!r}: author={author_login!r} committer={committer_login!r}")
    if verified is not True or reason!="valid":
      return False,f"commit {sha!r} is not GitHub-verified (verified={verified!r} reason={reason!r})"
  return True,None
def dependency_bump_exemption(changed,policy,pr_author,workflow_diff_text,commit_records_text,expected_commit_count):
  # Narrow, fail-closed carve-out for Dependabot pin bumps: the requirement above (a NEW
  # trace changed in every high-risk PR) can never be satisfied by Dependabot, which only
  # ever edits `uses:` pins and cannot author a trace file. Any condition below that cannot
  # be verified is treated as "not exempt". Threat model includes a write-access insider
  # pushing extra commits/diff content onto an otherwise-legitimate Dependabot branch/PR.
  cfg=policy.get("dependencyBumpExemption")
  if not isinstance(cfg,dict): return False,"no dependencyBumpExemption configured"
  trusted=set(cfg.get("trustedAuthors",[]))
  if not pr_author or pr_author not in trusted:
    return False,f"author {pr_author!r} is not a trusted dependency-bump author"
  commit_author_login=cfg.get("trustedCommitAuthorLogin")
  commit_committer_login=cfg.get("trustedCommitCommitterLogin")
  if not commit_author_login or not commit_committer_login:
    return False,"trustedCommitAuthorLogin/trustedCommitCommitterLogin not configured"
  ok,detail=commit_provenance_trusted(commit_records_text,expected_commit_count,commit_author_login,commit_committer_login)
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
    pin_re=re.compile(cfg.get("pinOnlyLineRe",r"^(?P<prefix>\s*-?\s*)uses:\s*(?P<ref>\S+)(?:\s*#.*)?$"))
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
  p.add_argument("--commits",help="path to a JSON-lines file, one {sha,author_login,committer_login,verified,reason} object per PR commit, used only to evaluate the dependency-bump exemption")
  p.add_argument("--expected-commit-count",type=int,help="github.event.pull_request.commits; the fetched --commits list must match this count exactly (the API caps pagination at 250)")
  a=p.parse_args()
  try:
    policy=load_policy(a.policy); changed=[x.strip() for x in Path(a.changed_files).read_text().splitlines() if x.strip()]
    risk,matches=classify(changed,policy); prefix=policy.get("tracePathPrefix",".agents/evals/traces/")
    trace=any(x.startswith(prefix) and x.endswith(".json") for x in changed); required=risk in set(policy.get("traceRequiredAt",["high"]))
    exempt=False; exemption_detail=None
    if required and not trace:
      exempt,exemption_detail=dependency_bump_exemption(changed,policy,a.pr_author,load_text(a.workflow_diff),load_text(a.commits),a.expected_commit_count)
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
