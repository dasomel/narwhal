#!/usr/bin/env python3
"""Assert every rendered Application fits the AppProject that claims it.

ArgoCD enforces sourceRepos and destinations at SYNC time, so an Application added
with a namespace the project does not list fails long after the commit that added it,
and reports "never synced" rather than "not allowed". This runs the same containment
check statically.

Renders the chart rather than grepping it: destinations and repoURLs come from Helm
values, so the template text does not contain the answer.
"""
import subprocess
import sys

import yaml

CHART = "gitops/charts/narwhal-apps"
PROJECTS = "gitops/resources/argocd-projects.yaml"
REPO = "http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git"


def render() -> list:
    out = subprocess.run(
        ["helm", "template", "narwhal-apps", CHART,
         "--set", f"repoURL={REPO}",
         "--set", "baseDomain=local.narwhal.internal",
         "--set", "provider=vagrant"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(f"helm template failed: {out.stderr.strip()[:300]}", file=sys.stderr)
        sys.exit(2)
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def namespace_allowed(ns: str, allowed: set) -> bool:
    # AppProject destinations take a trailing * as a prefix match.
    return ns in allowed or any(a.endswith("*") and ns.startswith(a[:-1]) for a in allowed)


def main() -> int:
    projects = {d["metadata"]["name"]: d["spec"]
                for d in yaml.safe_load_all(open(PROJECTS)) if d}
    problems = []

    for app in (d for d in render() if d.get("kind") == "Application"):
        name = app["metadata"]["name"]
        spec = app["spec"]
        proj = spec.get("project")
        if proj not in projects:
            problems.append(f"{name}: project '{proj}' is not defined in {PROJECTS}")
            continue
        policy = projects[proj]

        ns = (spec.get("destination") or {}).get("namespace", "")
        allowed = {d.get("namespace", "") for d in policy.get("destinations", [])}
        if not namespace_allowed(ns, allowed):
            problems.append(f"{name}: destination '{ns}' is not in project '{proj}'")

        sources = [spec["source"]] if spec.get("source") else spec.get("sources", [])
        for src in sources:
            url = src.get("repoURL", "")
            if url and url not in set(policy.get("sourceRepos", [])):
                problems.append(f"{name}: repoURL '{url}' is not in project '{proj}'")

    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
