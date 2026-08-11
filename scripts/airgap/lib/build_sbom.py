#!/usr/bin/env python3
"""Build a CycloneDX 1.5 SBOM from an assembled airgap bundle.

Invoked by 08-generate-sbom.sh. Kept as a separate file rather than a here-doc because it
is long enough that quoting it inside shell would be the bug, not the logic.

Everything here is read off the bundle itself — image digests from each OCI layout's
index.json, versions from chart and .deb filenames, sha256 for anything that is a single
file. Nothing is looked up over the network, because the machine that assembles a bundle
is often the last one that still has a route out.
"""

import argparse
import hashlib
import json
import pathlib
import re
import sys

CDX_VERSION = "1.5"


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def image_components(bundle: pathlib.Path) -> list:
    """One component per image ref in manifest.txt, digest from its OCI layout."""
    out = []
    manifest = bundle / "manifest.txt"
    for line in manifest.read_text().splitlines():
        ref = line.strip()
        if not ref or ref.startswith("#"):
            continue

        # 02-save-images.sh sanitises a ref into a directory name by replacing / and : with _
        layout = bundle / "oci" / re.sub(r"[/:]", "_", ref)
        digest = None
        index = layout / "index.json"
        if index.is_file():
            try:
                manifests = json.loads(index.read_text()).get("manifests") or []
                if manifests:
                    digest = manifests[0].get("digest")
            except (json.JSONDecodeError, OSError):
                digest = None

        name, _, version = ref.rpartition(":")
        if not name:                      # ref carried no tag
            name, version = ref, "latest"

        comp = {
            "type": "container",
            "name": name,
            "version": version,
            "purl": f"pkg:oci/{name.rsplit('/', 1)[-1]}?repository_url={name}&tag={version}",
        }
        if digest:
            comp["hashes"] = [{"alg": "SHA-256", "content": digest.removeprefix("sha256:")}]
            comp["purl"] += f"&digest={digest}"
        else:
            # Say so rather than emitting a component that looks verified but is not.
            comp["description"] = "digest unavailable: OCI layout missing or unreadable in bundle"
        out.append(comp)
    return out


def chart_components(bundle: pathlib.Path) -> list:
    """Helm charts: <name>-<semver>.tgz, so split on the last dash before a digit."""
    out = []
    for tgz in sorted((bundle / "charts").glob("*.tgz")):
        stem = tgz.name[: -len(".tgz")]
        m = re.match(r"^(?P<name>.+)-(?P<version>v?\d[^-]*)$", stem)
        name = m.group("name") if m else stem
        version = m.group("version") if m else "unknown"
        out.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:helm/{name}@{version}",
            "hashes": [{"alg": "SHA-256", "content": sha256_file(tgz)}],
        })
    return out


def deb_components(bundle: pathlib.Path) -> list:
    """OS packages: <name>_<version>_<arch>.deb (Debian's own filename convention)."""
    out = []
    apt = bundle / "apt"
    if not apt.is_dir():
        return out
    for deb in sorted(apt.glob("*.deb")):
        parts = deb.name[: -len(".deb")].split("_")
        if len(parts) >= 3:
            name, version, arch = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            name, version, arch = parts[0], parts[1], "unknown"
        else:
            name, version, arch = deb.stem, "unknown", "unknown"
        out.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:deb/ubuntu/{name}@{version}?arch={arch}",
            "hashes": [{"alg": "SHA-256", "content": sha256_file(deb)}],
        })
    return out


def file_components(bundle: pathlib.Path, subdir: str, ctype: str) -> list:
    """Binaries and remote manifests carry no version in the name — hash is the identity."""
    out = []
    d = bundle / subdir
    if not d.is_dir():
        return out
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        out.append({
            "type": ctype,
            "name": f.name,
            "version": "unversioned",
            "description": f"bundled {subdir}; identified by content hash, not by a declared version",
            "hashes": [{"alg": "SHA-256", "content": sha256_file(f)}],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    bundle = pathlib.Path(args.bundle)
    components = (
        image_components(bundle)
        + chart_components(bundle)
        + deb_components(bundle)
        + file_components(bundle, "bin", "application")
        + file_components(bundle, "manifests", "file")
    )

    # No timestamp: the document must be reproducible, so that regenerating it for an
    # unchanged bundle produces an identical file and a diff means the bundle changed.
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": CDX_VERSION,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": f"narwhal-airgap-bundle-{args.arch}",
                "description": (
                    "Offline installation bundle for the Narwhal Kubernetes IDP. "
                    "Bundle-level inventory: container images, Helm charts, binaries, remote "
                    "manifests and OS packages, each with the digest as bundled. This is NOT a "
                    "package-level SBOM of each image's filesystem — run syft against the OCI "
                    "layouts under oci/ for that."
                ),
            },
            "properties": [{"name": "narwhal:architecture", "value": args.arch}],
        },
        "components": components,
    }

    out = pathlib.Path(args.output)
    out.write_text(json.dumps(bom, indent=2, sort_keys=False) + "\n")

    kinds = {}
    for c in components:
        kinds[c["type"]] = kinds.get(c["type"], 0) + 1
    print("  components: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
    missing = sum(1 for c in components if "hashes" not in c)
    if missing:
        print(f"  WARN: {missing} component(s) have no digest", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
