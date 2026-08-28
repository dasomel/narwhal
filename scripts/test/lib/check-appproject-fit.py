#!/usr/bin/env python3
"""Assert every rendered Application fits the AppProject that claims it.

ArgoCD enforces sourceRepos and destinations at SYNC time, so an Application added
with a namespace the project does not list fails long after the commit that added it,
and reports "never synced" rather than "not allowed". This runs the same containment
check statically.

Renders the chart rather than grepping it: destinations and repoURLs come from Helm
values, so the template text does not contain the answer.
"""
import glob
import os
import subprocess
import sys
import tempfile

import yaml

CHART = "gitops/charts/narwhal-apps"
PROJECTS = "gitops/resources/argocd-projects.yaml"
REPO = "http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git"
GITOPS_ROOT = "gitops"


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


def yaml_namespaces(path: str) -> set:
    with open(path) as f:
        return {d.get("metadata", {}).get("namespace")
                for d in yaml.safe_load_all(f)
                if d and d.get("metadata", {}).get("namespace")}


def source_yaml_paths(local_path: str, directory: dict, allow_empty: bool) -> list:
    if not os.path.isdir(local_path):
        raise ValueError(f"directory source path does not exist: {local_path}")

    include = directory.get("include")
    if include:
        paths = glob.glob(os.path.join(local_path, include), recursive=True)
    elif directory.get("recurse"):
        paths = []
        for suffix in ("*.yaml", "*.yml", "*.json"):
            paths.extend(glob.glob(os.path.join(local_path, "**", suffix), recursive=True))
    else:
        paths = []
        for suffix in ("*.yaml", "*.yml", "*.json"):
            paths.extend(glob.glob(os.path.join(local_path, suffix)))

    paths = sorted(p for p in paths if os.path.isfile(p))
    if not paths and not allow_empty:
        selector = include or ("recursive YAML/JSON files" if directory.get("recurse") else "YAML/JSON files")
        raise ValueError(f"directory source matched no {selector}: {local_path}")
    return paths


def rendered_namespaces(name: str, source: dict, destination_ns: str, allow_empty: bool) -> set:
    """Return namespaced resources an Application manages beyond its destination.

    ArgoCD validates each rendered resource against its AppProject. A Helm chart or
    raw manifest may set metadata.namespace explicitly, so spec.destination alone is
    insufficient evidence of policy fit.
    """
    path = source.get("path")
    if not path:
        return set()
    local_path = os.path.join(GITOPS_ROOT, path)
    if source.get("helm"):
        args = ["helm", "template", name, local_path, "--namespace", destination_ns]
        values = source["helm"].get("valuesObject")
        values_path = None
        if values:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                yaml.safe_dump(values, f)
                values_path = f.name
            args.extend(["--values", values_path])
        try:
            out = subprocess.run(args, capture_output=True, text=True)
        finally:
            if values_path:
                os.unlink(values_path)
        if out.returncode != 0:
            print(f"{name}: helm template failed: {out.stderr.strip()[:300]}", file=sys.stderr)
            sys.exit(2)
        return {d.get("metadata", {}).get("namespace")
                for d in yaml.safe_load_all(out.stdout)
                if d and d.get("metadata", {}).get("namespace")}

    directory = source.get("directory")
    if directory is not None:
        paths = source_yaml_paths(local_path, directory, allow_empty)
        return set().union(*(yaml_namespaces(p) for p in paths))
    return set()


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

        allowed = {d.get("namespace", "") for d in policy.get("destinations", [])}
        destination_ns = (spec.get("destination") or {}).get("namespace", "")
        namespaces = {destination_ns}
        allow_empty = bool(((spec.get("syncPolicy") or {}).get("automated") or {}).get("allowEmpty"))

        sources = [spec["source"]] if spec.get("source") else spec.get("sources", [])
        for src in sources:
            try:
                namespaces.update(rendered_namespaces(name, src, destination_ns, allow_empty))
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
            url = src.get("repoURL", "")
            if url and url not in set(policy.get("sourceRepos", [])):
                problems.append(f"{name}: repoURL '{url}' is not in project '{proj}'")

        for ns in sorted(ns for ns in namespaces if ns):
            if not namespace_allowed(ns, allowed):
                problems.append(f"{name}: namespace '{ns}' is not in project '{proj}'")

    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
