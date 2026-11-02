"""R301: bootstrap dev has one rendered owner and ordered namespace adoption."""

import fnmatch
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WAVE = "argocd.argoproj.io/sync-wave"


def documents(text):
    return [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict)]


def render(chart):
    return documents(subprocess.check_output(
        ["helm", "template", chart.name, str(chart)], text=True))


def identity(doc):
    meta = doc.get("metadata", {})
    return doc.get("kind"), meta.get("namespace", ""), meta.get("name")


def main():
    gitops = ROOT / "gitops"
    resources = documents((gitops / "resources/dev-namespace.yaml").read_text())
    expected = {("Namespace", "", "dev"), ("ResourceQuota", "dev", "dev-quota"),
                ("LimitRange", "dev", "dev-limits")}
    assert len(resources) == 3 and {identity(doc) for doc in resources} == expected
    namespace = next(doc for doc in resources if doc["kind"] == "Namespace")
    assert namespace["metadata"]["labels"]["istio.io/dataplane-mode"] == "none"
    wave = lambda doc: int(doc["metadata"].get("annotations", {}).get(WAVE, "0"))
    assert all(wave(namespace) < wave(doc) for doc in resources if doc != namespace)

    rendered = []
    for chart in sorted((gitops / "charts").iterdir()):
        if (chart / "Chart.yaml").exists():
            rendered.extend(render(chart))
    apps = [doc for doc in rendered if doc.get("kind") == "Application"]
    for path in (gitops / "apps").glob("*.yaml"):
        apps.extend(documents(path.read_text()))
    owners = []
    for app in apps:
        spec = app.get("spec", {})
        for source in [spec.get("source", {}), *spec.get("sources", [])]:
            if source.get("path", "").rstrip("/") != "resources":
                continue
            directory = source.get("directory", {})
            includes = directory.get("include", "*").strip("{}").split(",")
            excludes = directory.get("exclude", "").strip("{}").split(",")
            if (any(fnmatch.fnmatch("dev-namespace.yaml", p) for p in includes)
                    and not any(fnmatch.fnmatch("dev-namespace.yaml", p) for p in excludes)):
                owners.append(app)
    assert len(owners) == 1, [app["metadata"]["name"] for app in owners]
    owner = owners[0]
    assert owner["metadata"]["name"] == "dev-namespace"
    assert owner["spec"]["project"] == "platform"
    assert "ServerSideApply=true" in owner["spec"]["syncPolicy"]["syncOptions"]
    assert wave(owner) < wave(next(a for a in apps if a["metadata"]["name"] == "rbac-policies"))

    # Compare every concrete resource and every local chart render, not just names
    # in this file: RBAC in dev belongs to another Application and stays there.
    all_docs = list(rendered)
    for path in (gitops / "resources").rglob("*.yaml"):
        all_docs.extend(documents(path.read_text()))
    for key in expected:
        matches = [doc for doc in all_docs if identity(doc) == key]
        assert len(matches) == 1, (key, len(matches))
    print("PASS: R301 one SSA owner, 3 unique objects, Namespace before quota/limits and RBAC")


if __name__ == "__main__":
    main()
