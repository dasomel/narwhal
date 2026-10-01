#!/usr/bin/env python3
"""Check upgrade bundle contents, dependencies, and supported component versions."""

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/common/compatibility-matrix.json"
VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.\d+)?(?:[-+].*)?$")


def load_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def sha256_path(path):
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    else:
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(child.relative_to(path).as_posix().encode())
            digest.update(b"\0")
            digest.update(child.read_bytes())
    return digest.hexdigest()


def chart_metadata(path):
    text = path.read_text(encoding="utf-8")
    dependencies = []
    in_dependencies = False
    for line in text.splitlines():
        if re.match(r"^dependencies:\s*(?:#.*)?$", line):
            in_dependencies = True
        elif in_dependencies and re.match(r"^\S", line):
            in_dependencies = False
        elif in_dependencies:
            match = re.match(r"^\s*-\s*name:\s*[\"']?([^\"'#\s]+)", line)
            if match:
                dependencies.append(match.group(1))
    images = set(re.findall(r"(?m)^\s*image:\s*[\"']?([^\"'\s]+)", text))
    return dependencies, images


def verify(bundle):
    errors = []
    warnings = []
    manifest_path = bundle / "manifest.json"
    try:
        manifest = load_json(manifest_path)
        matrix = load_json(MATRIX)
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL: cannot read manifest or compatibility matrix: {error}")
        print("Evaluated: artifacts=0, files=0, dependencies=0, versions=0")
        return 1
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("manifest artifacts are missing or empty")
        artifacts = []
    declared = {}
    files_checked = dependencies_checked = versions_checked = 0
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict) or not artifact.get("path"):
            errors.append(f"artifact {index} has no bundle-relative path")
            continue
        relative = Path(artifact["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"artifact {artifact.get('name', index)} has unsafe path {relative}")
            continue
        path = bundle / relative
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(bundle.resolve())
            if not resolved.is_file() and not (artifact.get("artifact_type") == "helm_chart" and resolved.is_dir()):
                raise OSError("not a regular file")
            digest = sha256_path(resolved)
        except (OSError, ValueError) as error:
            errors.append(f"artifact {artifact.get('name', index)} unreadable or missing: {error}")
            continue
        files_checked += 1
        expected = str(artifact.get("digest", ""))
        if expected != f"sha256:{digest}":
            errors.append(f"artifact {artifact.get('name', index)} sha256 mismatch")
        if relative.as_posix() in declared:
            errors.append(f"duplicate declared path: {relative}")
        declared[relative.as_posix()] = artifact

    present = set()
    try:
        present = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file() and p != manifest_path}
    except OSError as error:
        errors.append(f"cannot enumerate bundle files: {error}")
    covered = set(declared)
    for path, artifact in declared.items():
        if artifact.get("artifact_type") == "helm_chart":
            covered.update(item for item in present if item.startswith(path.rstrip("/") + "/"))
    for extra in sorted(present - covered):
        errors.append(f"undeclared bundle file: {extra}")

    by_name = {a.get("name"): a for a in artifacts if isinstance(a, dict) and a.get("name")}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("artifact_type") != "helm_chart":
            continue
        path = bundle / artifact.get("path", "")
        chart_file = path / "Chart.yaml" if path.is_dir() else None
        if chart_file is None or not chart_file.is_file():
            errors.append(f"chart {artifact.get('name')} must point to a directory containing Chart.yaml")
            continue
        try:
            deps, images = chart_metadata(chart_file)
            chart_files = [p for p in path.rglob("*") if p.is_file()]
            for chart_part in chart_files:
                if chart_part != chart_file:
                    _, found = chart_metadata(chart_part)
                    images.update(found)
        except OSError as error:
            errors.append(f"chart {artifact.get('name')} unreadable: {error}")
            continue
        names = set(by_name)
        for dep in deps:
            dependencies_checked += 1
            if dep not in names and not any(a.get("name", "").startswith(dep + "-") for a in artifacts if isinstance(a, dict)):
                errors.append(f"chart {artifact.get('name')} missing declared dependency {dep}")
        for image in images:
            dependencies_checked += 1
            image_name = image.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0]
            image_version = image.rsplit(":", 1)[-1] if ":" in image else ""
            if not any(a.get("artifact_type") == "container_image" and
                       (a.get("source_ref", "").split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0] == image_name or a.get("name") == image_name) and
                       (not image_version or a.get("version", "").lstrip("v") == image_version.lstrip("v"))
                       for a in artifacts if isinstance(a, dict)):
                errors.append(f"chart {artifact.get('name')} references absent image {image}")

    kube_version = manifest.get("target_version", "")
    kube_artifact = next((a for a in artifacts if isinstance(a, dict) and a.get("name") == "kubernetes"), None)
    if kube_artifact:
        kube_version = kube_artifact.get("version", kube_version)
    match = VERSION.fullmatch(str(kube_version))
    row = matrix.get("supportedRows", {}).get(f"{match.group(1)}.{match.group(2)}") if match else None
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("artifact_type") not in ("container_image", "helm_chart"):
            continue
        component = artifact.get("component", artifact.get("name"))
        if component not in {c for r in matrix.get("supportedRows", {}).values() for c in r}:
            warnings.append(f"unknown compatibility component: {component}")
            continue
        versions_checked += 1
        version = VERSION.fullmatch(str(artifact.get("version", "")))
        bounds = row.get(component) if row else None
        if not version or not bounds:
            errors.append(f"cannot evaluate {component} {artifact.get('version')} against Kubernetes {kube_version}")
            continue
        current = tuple(map(int, version.groups()))
        minimum = tuple(map(int, bounds["min"].split(".")))
        maximum = tuple(map(int, bounds.get("max", "999.999").split(".")))
        if not minimum <= current <= maximum:
            errors.append(f"{component} {artifact['version']} outside supported range {bounds}")
    print(f"Evaluated: artifacts={len(artifacts)}, files={files_checked}, dependencies={dependencies_checked}, versions={versions_checked}")
    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"FAIL: {error}")
    if not files_checked or not artifacts:
        errors.append("zero bundle artifacts/files evaluated")
    if errors:
        print(f"FAIL: upgrade bundle has {len(errors)} error(s)")
        return 1
    print("PASS: upgrade bundle contents and compatibility verified")
    return 0


def self_test():
    def fixture(root, *, unknown=False):
        (root / "chart/templates").mkdir(parents=True)
        (root / "image.tar").write_bytes(b"image")
        (root / "dep.pkg").write_bytes(b"dependency")
        (root / "chart/Chart.yaml").write_text("apiVersion: v2\nname: demo\nversion: 1.0.0\ndependencies:\n  - name: dep\n    version: 1.0.0\n")
        (root / "chart/templates/deploy.yaml").write_text("image: repo/demo:1.19.4\n")
        image = root / "image.tar"
        dependency = root / "dep.pkg"
        artifacts = [
            {"name": "demo-chart", "component": "istio", "version": "1.30.1", "artifact_type": "helm_chart", "path": "chart", "digest": "sha256:" + sha256_path(root / "chart")},
            {"name": "demo", "version": "1.19.4", "artifact_type": "container_image", "path": "image.tar", "source_ref": "repo/demo:1.19.4", "digest": "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest()},
            {"name": "dep", "version": "1.0.0", "artifact_type": "operator_package", "path": "dep.pkg", "digest": "sha256:" + hashlib.sha256(dependency.read_bytes()).hexdigest()},
        ]
        if unknown:
            artifacts[0]["component"] = "mystery"
        (root / "manifest.json").write_text(json.dumps({"target_version": "1.35.7", "artifacts": artifacts}))

    cases = ["good", "missing file", "tampered file", "undeclared file", "missing chart dependency", "out-of-range version", "unknown component", "empty manifest"]
    with tempfile.TemporaryDirectory(prefix="upgrade-bundle-check-") as temporary:
        base = Path(temporary)
        for case in cases:
            root = base / case.replace(" ", "-")
            root.mkdir()
            fixture(root, unknown=case == "unknown component")
            if case == "missing file": (root / "image.tar").unlink()
            if case == "tampered file": (root / "image.tar").write_bytes(b"tampered")
            if case == "undeclared file": (root / "extra").write_text("x")
            if case == "missing chart dependency":
                data = load_json(root / "manifest.json"); data["artifacts"] = data["artifacts"][:2]; (root / "manifest.json").write_text(json.dumps(data))
            if case == "out-of-range version":
                data = load_json(root / "manifest.json"); data["artifacts"][0]["version"] = "1.99"; (root / "manifest.json").write_text(json.dumps(data))
            if case == "empty manifest": (root / "manifest.json").write_text('{"artifacts":[]}')
            result = verify(root)
            should_pass = case == "good" or case == "unknown component"
            if (result == 0) != should_pass:
                raise RuntimeError(f"self-test failed: {case}")
            print(f"PASS: self-test {case}")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, help="upgrade bundle directory")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.root:
        parser.error("--root is required")
    return verify(args.root)


if __name__ == "__main__":
    sys.exit(main())
