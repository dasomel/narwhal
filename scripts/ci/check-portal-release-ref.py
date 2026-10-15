#!/usr/bin/env python3
"""Decide whether a workflow run may publish / release a portal version (PR #347 review, P2).

portal-release.yml used to trust `workflow_run.head_branch` starting with `portal/v`; a MANUAL
publish run (workflow_dispatch) on a BRANCH named `portal/v1.2.3` carries that same head_branch
and passed. A release must be provable from the run's own facts, not from a name.

  --mode release   (portal-release.yml, after workflow_run)
      event must be `push` (a tag push; dispatch/branch runs are refused), the name must be a
      portal version tag, the tag must exist and its commit must equal --sha.
  --mode publish   (portal-docker-publish.yml, first step)
      ref-type `tag`: the name must be a portal version tag whose commit equals --sha.
      ref-type `branch` (ad-hoc workflow_dispatch build): allowed, but a branch whose name looks
      like a release tag (`portal/v...`) is refused, so no later job can mistake it for one.

Exit 0 = allowed, 1 = refused (reason on stderr), 2 = usage error.
--repo PATH (default `.`) is the repository whose tags are consulted. portal-release.yml runs this
script from a checkout of the DEFAULT BRANCH (`trusted/`) and points --repo at the tagged checkout,
so a tagged commit cannot ship a validator that always passes (PR #347 follow-up, D-TRUST).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

TAG_RE = re.compile(r"^portal/v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def tag_commit(name: str, repo: str = ".") -> str | None:
    r = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{name}^{{commit}}"],
        text=True,
        capture_output=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def verdict(mode: str, event: str, ref_type: str, name: str, sha: str, repo: str = ".") -> str | None:
    """Return a refusal reason, or None when allowed."""
    if not SHA_RE.fullmatch(sha):
        return f"head sha '{sha}' is not a full 40-hex commit"
    if mode == "release":
        if event != "push":
            return f"release requires a tag push run, got event '{event}'"
        ref_type = "tag"
    elif ref_type == "branch":
        if name.startswith("portal/v"):
            return f"branch '{name}' is named like a release tag; refuse to build it"
        return None
    if ref_type != "tag":
        return f"ref type '{ref_type}' cannot publish a release"
    if not TAG_RE.fullmatch(name):
        return f"'{name}' is not a portal/vX.Y.Z tag"
    found = tag_commit(name, repo)
    if found is None:
        return f"tag '{name}' does not exist in this checkout (fetch tags first)"
    if found != sha:
        return f"tag '{name}' points at {found}, not the run's commit {sha}"
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=["release", "publish"])
    p.add_argument("--event", required=True)
    p.add_argument("--ref-type", default="tag", choices=["tag", "branch"])
    p.add_argument("--ref-name", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--repo", default=".", help="repository holding the tags (default: cwd)")
    a = p.parse_args()
    reason = verdict(a.mode, a.event, a.ref_type, a.ref_name, a.sha, a.repo)
    if reason:
        print(f"::error::{reason}", file=sys.stderr)
        return 1
    print(f"OK: {a.mode} of {a.ref_type} '{a.ref_name}' at {a.sha} is allowed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
