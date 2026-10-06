#!/usr/bin/env python3
"""Pre-merge independent-review gate.

Passes only when the PR is not a draft, carries the label `review:pass`, and that label was
(re)applied strictly AFTER the PR's latest change (newest commit committer date or newest
head_ref_force_pushed event). Any later push therefore invalidates an earlier review.
Fails closed on missing or malformed data.
"""
import json, os, subprocess, sys
from datetime import datetime

LABEL = "review:pass"
# The PR commits endpoint returns at most 250 commits; a larger list may be truncated.
COMMITS_API_CAP = 250


def parse_ts(value, what):
  if not isinstance(value, str) or not value:
    raise ValueError(f"missing timestamp for {what}")
  try:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    raise ValueError(f"unparseable timestamp for {what}: {value!r}")


def evaluate(pr, commits, timeline):
  """Return (ok, reason). Pure function over already-fetched API payloads."""
  if not isinstance(pr, dict) or not isinstance(commits, list) or not isinstance(timeline, list):
    return False, "malformed API data (expected PR object, commit list, timeline list)"
  if pr.get("draft") is not False:
    return False, "PR is a draft (or draft state unknown); mark it ready for review first"
  labels = pr.get("labels")
  if not isinstance(labels, list):
    return False, "PR labels missing from API data"
  if LABEL not in {l.get("name") for l in labels if isinstance(l, dict)}:
    return False, f"label '{LABEL}' is not on the PR; the independent reviewer applies it after a PASS"
  if not commits:
    return False, "no commits returned for the PR; cannot determine last change"
  if len(commits) >= COMMITS_API_CAP:
    return False, f"PR has {len(commits)} commits (API cap {COMMITS_API_CAP}); cannot prove the latest change"

  try:
    label_events = []
    for ev in timeline:
      if not isinstance(ev, dict) or ev.get("event") != "labeled":
        continue
      label = ev.get("label")
      if isinstance(label, dict) and label.get("name") == LABEL:
        label_events.append((parse_ts(ev.get("created_at"), "labeled event"), (ev.get("actor") or {}).get("login") or "unknown"))
    if not label_events:
      return False, f"label '{LABEL}' is present but no 'labeled' event was found in the timeline"
    label_time, labeler = max(label_events, key=lambda e: e[0])

    changes = []
    for c in commits:
      try:
        date = c["commit"]["committer"]["date"]
      except (KeyError, TypeError):
        return False, "commit entry without commit.committer.date; failing closed"
      changes.append((parse_ts(date, f"commit {str(c.get('sha', '?'))[:7]}"), "commit"))
    for ev in timeline:
      if isinstance(ev, dict) and ev.get("event") == "head_ref_force_pushed":
        changes.append((parse_ts(ev.get("created_at"), "head_ref_force_pushed event"), "force-push"))
    last_change, kind = max(changes, key=lambda e: e[0])
  except ValueError as e:
    return False, f"bad data: {e}"

  if label_time > last_change:
    return True, f"'{LABEL}' applied by {labeler} at {label_time.isoformat()} is after the last change ({kind} at {last_change.isoformat()})"
  return False, (f"'{LABEL}' applied by {labeler} at {label_time.isoformat()} is NOT after the last change "
                 f"({kind} at {last_change.isoformat()}); the review is stale, re-review and re-apply the label")


def parse_concatenated_json(text):
  """`gh api --paginate` prints one JSON document per page; merge arrays, keep a lone object."""
  dec, i, docs, text = json.JSONDecoder(), 0, [], text.strip()
  while i < len(text):
    doc, i = dec.raw_decode(text, i)
    docs.append(doc)
    while i < len(text) and text[i].isspace():
      i += 1
  if docs and all(isinstance(d, list) for d in docs):
    return [x for d in docs for x in d]
  if len(docs) == 1:
    return docs[0]
  raise ValueError("unexpected paginated payload shape")


def gh_api(path, paginate=False):
  cmd = ["gh", "api"] + (["--paginate"] if paginate else []) + [path]
  proc = subprocess.run(cmd, capture_output=True, text=True)
  if proc.returncode != 0:
    raise RuntimeError(f"gh api {path} failed: {proc.stderr.strip()}")
  return parse_concatenated_json(proc.stdout)


def main(argv=None):
  repo, number = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("PR_NUMBER", "")
  if not repo or not number.isdigit():
    print("independent-review: FAIL: GITHUB_REPOSITORY and numeric PR_NUMBER are required", file=sys.stderr)
    return 1
  try:
    pr = gh_api(f"repos/{repo}/pulls/{number}")
    commits = gh_api(f"repos/{repo}/pulls/{number}/commits?per_page=100", paginate=True)
    timeline = gh_api(f"repos/{repo}/issues/{number}/timeline?per_page=100", paginate=True)
  except (RuntimeError, ValueError) as e:
    print(f"independent-review: FAIL: {e}", file=sys.stderr)
    return 1
  ok, reason = evaluate(pr, commits, timeline)
  print(f"independent-review: {'PASS' if ok else 'FAIL'}: {reason}")
  return 0 if ok else 1


if __name__ == "__main__":
  sys.exit(main())
