#!/usr/bin/env python3
import argparse, json, re, subprocess, sys
from pathlib import Path

USES_RE = re.compile(
  r"^\s+(-\s+)?uses:\s+(?P<ref>[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)@(?P<sha>[0-9a-f]{40})\s+#\s+(?P<tag>v?[0-9][0-9A-Za-z.+-]*)\s*$"
)

def emit(result, report_out=None):
  text = json.dumps(result, indent=2)
  print(text)
  if report_out:
    p = Path(report_out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n", encoding="utf-8")
  return 0 if result.get("exempt") else 1

def resolve_upstream(bumps, resolver_json=None):
  reasons = []
  if resolver_json:
    try:
      mapping = json.loads(Path(resolver_json).read_text(encoding="utf-8"))
      if not isinstance(mapping, dict):
        return [f"resolver JSON must be a dictionary: {resolver_json}"]
    except Exception as e:
      return [f"failed to read resolver JSON: {e}"]
    for b in bumps:
      key = f"{b['owner']}/{b['repo']}@{b['tag']}"
      if key not in mapping:
        reasons.append(f"resolver missing entry for {key}")
      else:
        expected = str(mapping[key]).strip().lower()
        if b["newSha"].lower() != expected:
          reasons.append(f"SHA mismatch for {key}: expected {expected}, got {b['newSha']}")
    return reasons

  cache = {}
  for b in bumps:
    key = f"{b['owner']}/{b['repo']}@{b['tag']}"
    if key in cache:
      resolved_sha = cache[key]
    else:
      url = f"https://github.com/{b['owner']}/{b['repo']}"
      try:
        cp = subprocess.run(
          ["git", "ls-remote", url, f"refs/tags/{b['tag']}", f"refs/tags/{b['tag']}^{{}}"],
          capture_output=True, text=True, timeout=30, check=False
        )
      except subprocess.TimeoutExpired:
        reasons.append(f"git ls-remote timed out after 30s for {key}")
        continue
      except Exception as e:
        reasons.append(f"git ls-remote error for {key}: {e}")
        continue
      if cp.returncode != 0:
        reasons.append(f"git ls-remote failed (exit {cp.returncode}) for {key}: {cp.stderr.strip()}")
        continue
      peeled, plain = None, None
      for line in cp.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
          sha, ref_name = parts[0].strip().lower(), parts[1].strip()
          if ref_name == f"refs/tags/{b['tag']}^{{}}":
            peeled = sha
          elif ref_name == f"refs/tags/{b['tag']}":
            plain = sha
      resolved_sha = peeled if peeled is not None else plain
      cache[key] = resolved_sha
    if not resolved_sha:
      reasons.append(f"tag {b['tag']} not found on remote for {b['owner']}/{b['repo']}")
    elif b["newSha"].lower() != resolved_sha:
      reasons.append(f"SHA mismatch for {key}: upstream resolved to {resolved_sha}, diff has {b['newSha']}")
  return reasons

def check_bump(base, head, report_out=None, resolver_json=None):
  reasons = []
  cp_names = subprocess.run(
    ["git", "diff", "--name-only", "--no-ext-diff", base, head],
    capture_output=True, text=True, check=False
  )
  if cp_names.returncode != 0:
    return emit({"exempt": False, "reasons": [f"git diff --name-only failed: {cp_names.stderr.strip()}"]}, report_out)
  changed = [p.strip() for p in cp_names.stdout.splitlines() if p.strip()]
  if not changed:
    return emit({"exempt": False, "reasons": ["empty diff: no changed files"]}, report_out)

  for p in changed:
    parts = p.split("/")
    if len(parts) != 3 or parts[0] != ".github" or parts[1] != "workflows" or not (parts[2].endswith(".yml") or parts[2].endswith(".yaml")):
      reasons.append(f"changed path not an allowed workflow file: {p}")

  cp_diff = subprocess.run(
    ["git", "diff", "-U0", "--no-ext-diff", base, head],
    capture_output=True, text=True, check=False
  )
  if cp_diff.returncode != 0:
    return emit({"exempt": False, "reasons": reasons + [f"git diff -U0 failed: {cp_diff.stderr.strip()}"]}, report_out)

  files_diffs = []
  current = None
  for line in cp_diff.stdout.splitlines():
    if line.startswith("diff --git "):
      if current is not None:
        files_diffs.append(current)
      current = {"header": line, "metadata": [], "hunks": []}
    elif current is not None:
      if line.startswith("@@"):
        current["hunks"].append({"header": line, "lines": []})
      elif current["hunks"]:
        current["hunks"][-1]["lines"].append(line)
      else:
        current["metadata"].append(line)
  if current is not None:
    files_diffs.append(current)

  bumps = []
  forbidden = ("new file mode", "deleted file mode", "old mode", "new mode", "similarity index", "rename from", "rename to", "Binary files")
  for fd in files_diffs:
    for meta in fd["metadata"]:
      for pref in forbidden:
        if meta.startswith(pref):
          reasons.append(f"unsupported file change ({pref}): {fd['header']}")
    if not fd["hunks"]:
      reasons.append(f"no hunks found in {fd['header']}")
    for hunk in fd["hunks"]:
      removed, added = [], []
      for hline in hunk["lines"]:
        if hline.startswith("-"):
          removed.append(hline[1:])
        elif hline.startswith("+"):
          added.append(hline[1:])
        else:
          reasons.append(f"unexpected line in hunk: {hline!r}")
      if not removed and not added:
        reasons.append("empty hunk")
      elif not removed:
        reasons.append(f"pure additions in hunk: {len(added)} lines added")
      elif not added:
        reasons.append(f"pure deletions in hunk: {len(removed)} lines removed")
      elif len(removed) != len(added):
        reasons.append(f"hunk line count mismatch: {len(removed)} removed vs {len(added)} added")
      else:
        for rem_line, add_line in zip(removed, added):
          rem_m = USES_RE.match(rem_line)
          add_m = USES_RE.match(add_line)
          if not rem_m:
            reasons.append(f"removed line does not match uses regex: {rem_line!r}")
          if not add_m:
            reasons.append(f"added line does not match uses regex: {add_line!r}")
          if rem_m and add_m:
            rem_ws = re.match(r"^\s*", rem_line).group(0)
            add_ws = re.match(r"^\s*", add_line).group(0)
            if rem_ws != add_ws:
              reasons.append(f"indentation change: {rem_ws!r} vs {add_ws!r}")
            if (rem_m.group(1) or "") != (add_m.group(1) or ""):
              reasons.append(f"list marker change: {rem_m.group(1)!r} vs {add_m.group(1)!r}")
            rem_ref, add_ref = rem_m.group("ref"), add_m.group("ref")
            if rem_ref != add_ref:
              reasons.append(f"ref changed: {rem_ref} -> {add_ref}")
            rem_norm = rem_line[:rem_m.start("sha")] + "<SHA>" + rem_line[rem_m.end("sha"):rem_m.start("tag")] + "<TAG>" + rem_line[rem_m.end("tag"):]
            add_norm = add_line[:add_m.start("sha")] + "<SHA>" + add_line[add_m.end("sha"):add_m.start("tag")] + "<TAG>" + add_line[add_m.end("tag"):]
            if rem_norm != add_norm:
              reasons.append(f"line difference beyond sha and tag: {rem_line!r} vs {add_line!r}")
            rem_sha, add_sha = rem_m.group("sha"), add_m.group("sha")
            rem_tag, add_tag = rem_m.group("tag"), add_m.group("tag")
            if rem_sha == add_sha and rem_tag == add_tag:
              reasons.append("sha and tag did not change")
            ref_parts = add_ref.split("/")
            if len(ref_parts) < 2:
              reasons.append(f"ref must have owner/repo: {add_ref}")
            else:
              bumps.append({
                "ref": add_ref,
                "owner": ref_parts[0],
                "repo": ref_parts[1],
                "oldSha": rem_sha,
                "newSha": add_sha,
                "oldTag": rem_tag,
                "tag": add_tag
              })

  if not bumps and not reasons:
    reasons.append("no action bump lines found in diff")

  if reasons:
    return emit({"exempt": False, "reasons": reasons}, report_out)

  res_reasons = resolve_upstream(bumps, resolver_json)
  if res_reasons:
    return emit({"exempt": False, "reasons": res_reasons}, report_out)

  return emit({"exempt": True, "reasons": [], "bumps": bumps}, report_out)

def main():
  p = argparse.ArgumentParser(description="Check if PR diff is an exempt Dependabot action bump")
  p.add_argument("--base", required=True, help="Base commit SHA")
  p.add_argument("--head", required=True, help="Head commit SHA")
  p.add_argument("--report-out", help="Optional report output path")
  p.add_argument("--resolver-json", help="Optional JSON file mapping owner/repo@tag -> sha for offline test resolution")
  a = p.parse_args()
  return check_bump(a.base, a.head, a.report_out, a.resolver_json)

if __name__ == "__main__":
  raise SystemExit(main())
