#!/usr/bin/env python3
import argparse,re,shlex,sys
from pathlib import Path
ACTION_RE=re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
SHA40_RE=re.compile(r"^[0-9a-f]{40}$")
PATTERNS=(("container latest tag",re.compile(r":latest\b")),("latest release download",re.compile(r"/releases/latest(?:/download)?(?:/|\b)")))
def files(paths):
  for raw in paths:
    p=Path(raw)
    if not p.exists(): raise FileNotFoundError(raw)
    if p.is_file(): yield p
    else:
      for c in sorted(p.rglob('*')):
        if c.is_file() and '.git' not in c.parts: yield c
EXACT_VERSION_RE=re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
PYTHON_VERSION_RE=re.compile(r"^(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+[0-9A-Za-z.-]+)?$")
INSTALL_RE=re.compile(r"\b(?:(npm|pnpm)\s+(?:install|add)|(?:python[0-9.]*\s+-m\s+)?(pip[0-9]*)\s+install)\s+([^;&|]+)")
def installation_findings(line):
  out=[]
  # D1: fail on literal floating packages, preserving lockfile/requirements installs.
  # Cost: shell variables are unresolved; use reviewed exact literals or lockfiles.
  for match in INSTALL_RE.finditer(line):
    try: tokens=shlex.split(match.group(3), comments=True)
    except ValueError:
      out.append('package install cannot be parsed'); continue
    requirements=False
    skip=False
    for token in tokens:
      if skip: skip=False; continue
      if token in ('-r','--requirement','-c','--constraint'):
        requirements=True; skip=True; continue
      if token in ('--prefix','--registry','--index-url','--extra-index-url','--cache-dir'):
        skip=True; continue
      if token.startswith('-'): continue
      if token.startswith(('./','../')): continue
      if match.group(1):
        version=token.rsplit('@',1)[1] if '@' in token.lstrip('@') else ''
      else:
        version=token.rsplit('==',1)[1] if '==' in token else ''
      version_pattern=EXACT_VERSION_RE if match.group(1) else PYTHON_VERSION_RE
      if not version_pattern.fullmatch(version):
        out.append('third-party package install is not an exact version: '+token)
    if requirements and '--require-hashes' not in tokens:
      out.append('requirements install does not enforce --require-hashes')
  if re.search(r"\b(?:curl|wget)\b[^\n]*\|\s*(?:bash|sh|tar)\b",line):
    out.append('download is executed/extracted before checksum verification')
  if re.search(r"/archive/refs/(?:heads|tags)/",line):
    out.append('archive download uses a mutable git ref')
  return out

def scan(p):
  out=[]
  try: lines=p.read_text(encoding='utf-8').splitlines()
  except UnicodeDecodeError: return out
  for n,line in enumerate(lines,1):
    s=line.strip()
    if not s or s.startswith('#'): continue
    for label,pattern in PATTERNS:
      if pattern.search(line): out.append((n,label,s))
    for label in installation_findings(line): out.append((n,label,s))
    m=ACTION_RE.match(line)
    if m:
      ref=m.group(1)
      if ref.startswith('./'): continue
      if '@' not in ref or not SHA40_RE.fullmatch(ref.rsplit('@',1)[1]): out.append((n,'GitHub Action ref is not a 40-char commit SHA',s))
  return out
def main():
  ap=argparse.ArgumentParser(); ap.add_argument('paths',nargs='+'); args=ap.parse_args(); bad=[]
  try: protected=list(files(args.paths))
  except FileNotFoundError as e:
    print(f'ERROR: protected path does not exist: {e}',file=sys.stderr); return 2
  for p in protected:
    for n,label,text in scan(p): bad.append((p,n,label,text))
  if bad:
    for p,n,label,text in bad: print(f'{p}:{n}: {label}: {text}',file=sys.stderr)
    return 1
  print(f'Mutable-input guard passed for {len(protected)} file(s)'); return 0
if __name__=='__main__': raise SystemExit(main())
