#!/usr/bin/env python3
"""R375: Workload compliance hardening round 5 (platform-system/apisix KSV014).

Pins:
1. platform-system/apisix (scripts/cluster/08-1-networking.sh & gitops/charts/narwhal-apps/templates/apisix.yaml):
   - Top-level securityContext sets readOnlyRootFilesystem=true alongside existing
     allowPrivilegeEscalation=false, runAsNonRoot=true, runAsUser=636, drop=[ALL],
     and seccompProfile.type=RuntimeDefault.
   - extraInitContainers runs init-conf with image apache/apisix:3.15.0-ubuntu
     (matching the rendered main container image), the same hardened securityContext,
     bounded resources, and `cp -R /usr/local/apisix/conf/. /conf-rw/` (not `cp -a`,
     which fails on /conf-rw/. mtimes).
   - extraVolumes / extraVolumeMounts provide emptyDir mounts for
     /usr/local/apisix/conf (listed before /usr/local/apisix/conf/cert/narwhal in GitOps),
     /usr/local/apisix/logs, /tmp, and /usr/local/apisix/{client_body,proxy,fastcgi,uwsgi,scgi}_temp.
2. Offline helm template renders for apisix-2.13.0.tgz (both 08-1-networking.sh and
   GitOps apisix.yaml) when an airgap bundle is present.
"""
import argparse
import glob
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "net_sh": "scripts/cluster/08-1-networking.sh",
    "apisix_yaml": "gitops/charts/narwhal-apps/templates/apisix.yaml",
}
ERRORS = (AssertionError, KeyError, TypeError, ValueError, yaml.YAMLError)

EXPECTED_IMAGE = "apache/apisix:3.15.0-ubuntu"
EXPECTED_WRITABLE_MOUNTS = (
    ("apisix-conf", "/usr/local/apisix/conf"),
    ("apisix-logs", "/usr/local/apisix/logs"),
    ("tmp", "/tmp"),
    ("apisix-client-body-temp", "/usr/local/apisix/client_body_temp"),
    ("apisix-proxy-temp", "/usr/local/apisix/proxy_temp"),
    ("apisix-fastcgi-temp", "/usr/local/apisix/fastcgi_temp"),
    ("apisix-uwsgi-temp", "/usr/local/apisix/uwsgi_temp"),
    ("apisix-scgi-temp", "/usr/local/apisix/scgi_temp"),
)


def _extract_08_1_values(text: str) -> dict:
    marker = "cat > /tmp/apisix-values.yaml << 'EOF'\n"
    assert text.count(marker) == 1, "08-1-networking.sh: expected one apisix-values heredoc"
    body = text.split(marker, 1)[1].split("\nEOF\n", 1)[0]
    return yaml.safe_load(body.replace("${POD_NETWORK_CIDR}", "10.244.0.0/16"))


def _extract_gitops_values(text: str) -> dict:
    cleaned = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.S)
    cleaned = re.sub(r"(?m)^\s*\{\{-?\s*(?:if|else|end)\b.*?-?\}\}\s*$", "", cleaned)
    cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", cleaned)
    return yaml.safe_load(cleaned)["spec"]["source"]["helm"]["valuesObject"]


def _find_chart(root: Path, filename: str) -> Path | None:
    for base in (root, root.parents[2]):
        matches = sorted(glob.glob(str(base / f"narwhal-airgap-bundle-*/charts/{filename}")))
        if matches:
            return Path(matches[0])
    return None


def _verify_hardened_sc(sc: dict, label: str) -> None:
    assert sc.get("allowPrivilegeEscalation") is False, f"{label}: allowPrivilegeEscalation must be false"
    assert sc.get("readOnlyRootFilesystem") is True, f"{label}: readOnlyRootFilesystem must be true"
    assert sc.get("runAsNonRoot") is True, f"{label}: runAsNonRoot must be true"
    assert sc.get("runAsUser") == 636, f"{label}: runAsUser must be 636"
    assert (sc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label}: capabilities.drop must be ['ALL']"
    assert (sc.get("seccompProfile") or {}).get("type") == "RuntimeDefault", (
        f"{label}: seccompProfile.type must be RuntimeDefault"
    )


def _verify_apisix_values(vals: dict, label: str, *, expect_root_ca: bool) -> None:
    _verify_hardened_sc(vals.get("securityContext") or {}, f"{label} main securityContext")

    inits = vals.get("extraInitContainers") or []
    assert isinstance(inits, list) and len(inits) == 1, f"{label}: expected 1 extraInitContainer"
    ic = inits[0]
    assert ic.get("name") == "init-conf", f"{label}: expected init-conf initContainer"
    assert ic.get("image") == EXPECTED_IMAGE, f"{label}: init-conf image {ic.get('image')!r} != {EXPECTED_IMAGE!r}"
    _verify_hardened_sc(ic.get("securityContext") or {}, f"{label} init-conf securityContext")

    cmd_str = " ".join(ic.get("command") or [])
    assert "cp -R /usr/local/apisix/conf/. /conf-rw/" in cmd_str, (
        f"{label}: init-conf must copy conf via cp -R, got {cmd_str!r}"
    )
    assert "cp -a" not in cmd_str, f"{label}: init-conf must not use cp -a"

    res = ic.get("resources") or {}
    assert (res.get("requests") or {}).get("cpu") and (res.get("requests") or {}).get("memory"), (
        f"{label}: init-conf must set resource requests"
    )
    assert (res.get("limits") or {}).get("cpu") and (res.get("limits") or {}).get("memory"), (
        f"{label}: init-conf must set resource limits"
    )

    ic_mounts = ic.get("volumeMounts") or []
    assert ic_mounts == [{"name": "apisix-conf", "mountPath": "/conf-rw"}], (
        f"{label}: init-conf volumeMounts unexpected: {ic_mounts!r}"
    )

    vols = {
        v["name"]: v for v in (vals.get("extraVolumes") or [])
        if isinstance(v, dict) and "name" in v
    }
    mounts = [m for m in (vals.get("extraVolumeMounts") or []) if isinstance(m, dict)]
    mount_paths = [m.get("mountPath") for m in mounts]
    for vol_name, mount_path in EXPECTED_WRITABLE_MOUNTS:
        assert vols.get(vol_name, {}).get("emptyDir") == {}, (
            f"{label}: volume {vol_name!r} for {mount_path} must be emptyDir {{}}"
        )
        matched = [m for m in mounts if m.get("mountPath") == mount_path]
        assert len(matched) == 1, f"{label}: expected exactly 1 mount at {mount_path}"
        assert matched[0].get("name") == vol_name, (
            f"{label}: {mount_path} volume {matched[0].get('name')!r} != {vol_name!r}"
        )

    if expect_root_ca:
        assert (vols.get("narwhal-root-ca") or {}).get("secret", {}).get("secretName") == "narwhal-root-ca-secret", (
            f"{label}: narwhal-root-ca volume missing"
        )
        ca_path = "/usr/local/apisix/conf/cert/narwhal"
        assert ca_path in mount_paths, f"{label}: {ca_path} mount missing"
        assert mount_paths.index("/usr/local/apisix/conf") < mount_paths.index(ca_path), (
            f"{label}: /usr/local/apisix/conf must be listed before {ca_path}"
        )


def verify(root: Path) -> None:
    net_vals = _extract_08_1_values((root / FILES["net_sh"]).read_text())
    gitops_vals = _extract_gitops_values((root / FILES["apisix_yaml"]).read_text())
    _verify_apisix_values(net_vals, "08-1", expect_root_ca=False)
    _verify_apisix_values(gitops_vals, "gitops", expect_root_ca=True)


def verify_helm_renders(root: Path) -> bool:
    chart_tgz = _find_chart(root, "apisix-2.13.0.tgz")
    if not chart_tgz:
        return False

    net_vals = _extract_08_1_values((root / FILES["net_sh"]).read_text())
    apps_out = subprocess.run(
        [
            "helm", "template", "test-apps", str(root / "gitops/charts/narwhal-apps"),
            "--set", "baseDomain=local.narwhal.internal",
            "--show-only", "templates/apisix.yaml",
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    gitops_vals = yaml.safe_load(apps_out)["spec"]["source"]["helm"]["valuesObject"]

    for label, vals, expect_ca in (("08-1", net_vals, False), ("gitops", gitops_vals, True)):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            yaml.dump(vals, f)
            f.flush()
            out = subprocess.run(
                [
                    "helm", "template", "apisix", str(chart_tgz),
                    "-n", "platform-system", "-f", f.name,
                    "--show-only", "templates/deployment.yaml",
                ],
                capture_output=True, text=True, check=True,
            ).stdout
        dep = yaml.safe_load(out)
        pspec = dep["spec"]["template"]["spec"]
        main_c = pspec["containers"][0]
        assert main_c["image"] == EXPECTED_IMAGE, f"{label} render: main image {main_c['image']!r}"
        _verify_hardened_sc(main_c["securityContext"], f"{label} render main")

        inits = pspec.get("initContainers") or []
        assert len(inits) == 1 and inits[0]["name"] == "init-conf", f"{label} render: init-conf missing"
        assert inits[0]["image"] == main_c["image"], f"{label} render: init image mismatch"
        _verify_hardened_sc(inits[0]["securityContext"], f"{label} render init-conf")

        vols = {v["name"]: v for v in pspec["volumes"]}
        mounts = {m["mountPath"]: m for m in main_c["volumeMounts"]}
        assert mounts.get("/usr/local/apisix/conf/config.yaml", {}).get("subPath") == "config.yaml", (
            f"{label} render: config.yaml subPath mount missing"
        )
        for vol_name, mount_path in EXPECTED_WRITABLE_MOUNTS:
            assert vols.get(vol_name, {}).get("emptyDir") == {}, f"{label} render: missing emptyDir {vol_name}"
            assert mounts.get(mount_path, {}).get("name") == vol_name, f"{label} render: missing mount {mount_path}"
        if expect_ca:
            assert mounts.get("/usr/local/apisix/conf/cert/narwhal", {}).get("name") == "narwhal-root-ca", (
                f"{label} render: narwhal-root-ca mount missing"
            )
    return True


def mutation_verify(root: Path) -> int:
    sources = {k: (root / rel).read_text() for k, rel in FILES.items()}
    mutations = [
        ("net_sh", "securityContext:\n  allowPrivilegeEscalation: false\n  readOnlyRootFilesystem: true", "securityContext:\n  allowPrivilegeEscalation: false\n  readOnlyRootFilesystem: false"),
        ("net_sh", "  - name: apisix-conf\n    mountPath: /usr/local/apisix/conf\n", ""),
        ("net_sh", "cp -R /usr/local/apisix/conf/. /conf-rw/", "cp -a /usr/local/apisix/conf/. /conf-rw/"),
        ("net_sh", "  - name: apisix-client-body-temp\n    mountPath: /usr/local/apisix/client_body_temp\n", ""),
        ("net_sh", "image: \"apache/apisix:3.15.0-ubuntu\"", "image: \"apache/apisix:3.15.0-debian\""),
        ("apisix_yaml", "        securityContext:\n          allowPrivilegeEscalation: false\n          readOnlyRootFilesystem: true", "        securityContext:\n          allowPrivilegeEscalation: false\n          readOnlyRootFilesystem: false"),
        ("apisix_yaml", "          - name: apisix-conf\n            mountPath: /usr/local/apisix/conf\n", ""),
        ("apisix_yaml", "cp -R /usr/local/apisix/conf/. /conf-rw/", "cp -a /usr/local/apisix/conf/. /conf-rw/"),
        ("apisix_yaml", "          - name: apisix-scgi-temp\n            mountPath: /usr/local/apisix/scgi_temp\n", ""),
        (
            "apisix_yaml",
            "        extraVolumeMounts:\n          - name: apisix-conf\n            mountPath: /usr/local/apisix/conf\n          - name: narwhal-root-ca\n            mountPath: /usr/local/apisix/conf/cert/narwhal\n            readOnly: true",
            "        extraVolumeMounts:\n          - name: narwhal-root-ca\n            mountPath: /usr/local/apisix/conf/cert/narwhal\n            readOnly: true\n          - name: apisix-conf\n            mountPath: /usr/local/apisix/conf",
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp)
        for rel in FILES.values():
            (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
        for key, old, new in mutations:
            for k, rel in FILES.items():
                (fixture / rel).write_text(sources[k])
            assert old in sources[key], f"mutation anchor missing in {key}: {old!r}"
            (fixture / FILES[key]).write_text(sources[key].replace(old, new, 1))
            try:
                verify(fixture)
            except ERRORS:
                continue
            raise AssertionError(f"mutation survived: {key}: {old!r}")
    return len(mutations)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    verify(args.root)
    rendered = verify_helm_renders(args.root)
    renders = "offline helm renders" if rendered else "offline helm renders SKIPPED (no chart bundle)"
    if args.mutation_verify:
        caught = mutation_verify(args.root)
        print(f"PASS: R375 round-5 compliance hardening + {renders} ({caught} mutations rejected)")
    else:
        print(f"PASS: R375 round-5 compliance hardening + {renders}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
