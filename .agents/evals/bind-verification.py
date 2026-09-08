#!/usr/bin/env python3
"""Bind a real command result into strict verification events."""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path
ALLOWED={"verification","regression_verification"}
def main():
 p=argparse.ArgumentParser();p.add_argument("--trace",required=True);p.add_argument("--event-id",action="append",required=True);p.add_argument("--out",required=True);p.add_argument("command",nargs=argparse.REMAINDER);a=p.parse_args();cmd=a.command[1:] if a.command and a.command[0]=="--" else a.command
 if not cmd: print("ERROR: command is required",file=sys.stderr);return 2
 try:t=json.loads(Path(a.trace).read_text(encoding="utf-8"))
 except Exception as e: print(f"ERROR: {e}",file=sys.stderr);return 2
 if t.get("consistencyMode")!="strict": print("ERROR: dynamic verification binding requires consistencyMode=strict",file=sys.stderr);return 2
 by={e.get("id"):e for e in t.get("events",[])};targets=[]
 for eid in a.event_id:
  e=by.get(eid)
  if not e or e.get("type") not in ALLOWED: print(f"ERROR: {eid} must identify verification/regression_verification",file=sys.stderr);return 2
  targets.append(e)
 r=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 if r.stdout: print(r.stdout,end="")
 st="passed" if r.returncode==0 else "failed"
 for e in targets:
  e["status"]=st;e["commandExitCode"]=r.returncode;refs=e.setdefault("evidence",[]);ref=f"runtime:command-exit-{r.returncode}"
  if ref not in refs: refs.append(ref)
 out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True);text=json.dumps(t,indent=2,ensure_ascii=False)+"\n"
 if out.resolve()==Path(a.trace).resolve():
  with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=out.parent,delete=False) as f:f.write(text);tmp=Path(f.name)
  tmp.replace(out)
 else: out.write_text(text,encoding="utf-8")
 print(f"Bound {','.join(a.event_id)} status={st} commandExitCode={r.returncode}");return 0
if __name__=="__main__": raise SystemExit(main())
