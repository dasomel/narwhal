#!/usr/bin/env python3
"""R371: Workload compliance hardening round 4 (Falco, Velero node-agent, nfs-quota-agent).

Pins:
1. security-system/falco (falco.yaml; GitOps-only, no Phase 2 bootstrap install):
   driver.modernEbpf.leastPrivileged=true, podSecurityContext.seccompProfile.type=RuntimeDefault,
   and containerSecurityContext with allowPrivilegeEscalation=false, readOnlyRootFilesystem=true,
   capabilities.drop=[ALL], capabilities.add=[BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE], and no
   privileged override (D-FALCO-LEAST-PRIV + D-falco-rofs).
2. storage/velero node-agent (velero.yaml & 08-4-storage.sh):
   nodeAgent.containerSecurityContext is exactly readOnlyRootFilesystem=true and
   capabilities.drop=[ALL], with no privileged override, and emptyDirs
   node-agent-home (/root) and tmp (/tmp), preserving podSecurityContext.runAsNonRoot=false.
3. nfs-quota-agent (05-nfs-quota-agent.sh; no GitOps Application owner):
   HELM_ARGS passes podSecurityContext.seccompProfile.type=RuntimeDefault,
   securityContext.privileged=true, and securityContext.readOnlyRootFilesystem=true
   directly through the chart's values.
4. Offline helm template renders for falco-9.2.1.tgz, velero-12.0.3.tgz, and
   nfs-quota-agent-0.0.0-main.tgz when an airgap bundle is present.
"""
import argparse
import glob
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "falco_yaml": "gitops/charts/narwhal-apps/templates/falco.yaml",
    "velero_yaml": "gitops/charts/narwhal-apps/templates/velero.yaml",
    "storage_sh": "scripts/cluster/08-4-storage.sh",
    "nqa_sh": "scripts/cluster/05-nfs-quota-agent.sh",
    "sec_sh": "scripts/cluster/08-3-security.sh",
}
ERRORS = (AssertionError, KeyError, TypeError, ValueError, yaml.YAMLError)
HELM_COMMENT_RE = re.compile(r"\A\{\{-\s*/\*.*?\*/\s*-?\}\}\s*", re.S)


def _app_values(text: str) -> dict:
    body = HELM_COMMENT_RE.sub("", text, count=1)
    cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", body)
    return yaml.safe_load(cleaned)["spec"]["source"]["helm"]["valuesObject"]


def _extract_velero_heredoc(text: str) -> dict:
    marker = "cat > /tmp/velero-values.yaml << EOF\n"
    assert text.count(marker) == 1, "08-4-storage.sh: expected one velero values heredoc"
    return yaml.safe_load(text.split(marker, 1)[1].split("\nEOF\n", 1)[0])


def _extract_nqa_helm_args(text: str) -> list[str]:
    m = re.search(r"HELM_ARGS=\(\n(.*?)\n\)", text, re.S)
    assert m, "05-nfs-quota-agent.sh: HELM_ARGS array not found"
    active_lines = [
        line for line in m.group(1).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    raw = "\n".join(active_lines)
    raw = raw.replace("${NFS_QUOTA_AGENT_VERSION}", "v0.5.0").replace("${NFS_SHARE_PATH}", "/srv/nfs/k8s")
    return shlex.split(raw)


def _find_chart(root: Path, filename: str) -> Path | None:
    for base in (root, root.parents[2]):
        matches = sorted(glob.glob(str(base / f"narwhal-airgap-bundle-*/charts/{filename}")))
        if matches:
            return Path(matches[0])
    return None


def _verify_falco(falco_yaml: str, sec_sh: str) -> None:
    vals = _app_values(falco_yaml)
    driver = vals.get("driver") or {}
    assert (driver.get("modernEbpf") or {}).get("leastPrivileged") is True, (
        "falco.yaml: driver.modernEbpf.leastPrivileged must be true"
    )
    psc = vals.get("podSecurityContext") or {}
    assert psc.get("seccompProfile") == {"type": "RuntimeDefault"}, (
        "falco.yaml: podSecurityContext.seccompProfile must be {type: RuntimeDefault}"
    )
    csc = vals.get("containerSecurityContext") or {}
    assert "privileged" not in csc, (
        "falco.yaml: containerSecurityContext must not set privileged under leastPrivileged"
    )
    assert csc.get("allowPrivilegeEscalation") is False, (
        "falco.yaml: containerSecurityContext.allowPrivilegeEscalation must be false"
    )
    assert csc.get("readOnlyRootFilesystem") is True, (
        "falco.yaml: containerSecurityContext.readOnlyRootFilesystem must be true"
    )
    assert csc.get("capabilities") == {
        "drop": ["ALL"],
        "add": ["BPF", "SYS_RESOURCE", "PERFMON", "SYS_PTRACE"],
    }, "falco.yaml: containerSecurityContext.capabilities must drop ALL and add [BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE]"
    assert "falco" not in sec_sh.lower(), "08-3-security.sh must not install a parallel bootstrap Falco"


def _verify_node_agent_values(na: dict, label: str) -> None:
    assert (na.get("podSecurityContext") or {}).get("runAsNonRoot") is False, (
        f"{label} nodeAgent.podSecurityContext.runAsNonRoot must remain false"
    )
    csc = na.get("containerSecurityContext") or {}
    assert csc.get("readOnlyRootFilesystem") is True, (
        f"{label} nodeAgent.containerSecurityContext.readOnlyRootFilesystem must be true"
    )
    assert csc.get("capabilities") == {"drop": ["ALL"]}, (
        f"{label} nodeAgent.containerSecurityContext.capabilities must drop ALL"
    )
    assert "privileged" not in csc, f"{label} nodeAgent must not set privileged"
    assert "privileged" not in na, f"{label} nodeAgent must not set the ignored privileged value"
    vols = {
        v["name"]: v for v in (na.get("extraVolumes") or [])
        if isinstance(v, dict) and "name" in v
    }
    mounts = [m for m in (na.get("extraVolumeMounts") or []) if isinstance(m, dict)]
    for mount_path, expected_vol in (("/root", "node-agent-home"), ("/tmp", "tmp")):
        matched = [m for m in mounts if m.get("mountPath") == mount_path]
        assert len(matched) == 1, f"{label} nodeAgent must mount {mount_path} exactly once"
        vname = matched[0].get("name")
        assert vname == expected_vol, f"{label} nodeAgent {mount_path} volume {vname!r} != {expected_vol!r}"
        assert vols.get(vname, {}).get("emptyDir") == {}, (
            f"{label} nodeAgent volume {vname!r} for {mount_path} must be emptyDir"
        )


def _verify_velero_node_agent(velero_yaml: str, storage_sh: str) -> None:
    _verify_node_agent_values(_app_values(velero_yaml).get("nodeAgent") or {}, "gitops")
    _verify_node_agent_values(_extract_velero_heredoc(storage_sh).get("nodeAgent") or {}, "08-4")


def _verify_nfs_quota_agent(root: Path, nqa_sh: str) -> None:
    args = _extract_nqa_helm_args(nqa_sh)
    set_pairs = [args[i + 1] for i, a in enumerate(args) if a == "--set" and i + 1 < len(args)]
    for expected in (
        "podSecurityContext.seccompProfile.type=RuntimeDefault",
        "securityContext.privileged=true",
        "securityContext.readOnlyRootFilesystem=true",
    ):
        assert expected in set_pairs, f"05-nfs-quota-agent.sh HELM_ARGS missing --set {expected}"

    assert 'nqa_check_skew "${CHART_DIR}" "${NFS_QUOTA_AGENT_VERSION}" "${HELM_ARGS[@]}"' in nqa_sh
    assert 'helm upgrade --install nfs-quota-agent "${CHART_DIR}" "${HELM_ARGS[@]}"' in nqa_sh

    gitops_apps = root / "gitops/charts/narwhal-apps/templates"
    if gitops_apps.is_dir():
        for tpl in gitops_apps.glob("*.yaml"):
            assert "chart: nfs-quota-agent" not in tpl.read_text(), (
                f"{tpl.name} unexpectedly owns nfs-quota-agent in GitOps"
            )


def verify(root: Path) -> None:
    texts = {k: (root / rel).read_text() for k, rel in FILES.items()}
    _verify_falco(texts["falco_yaml"], texts["sec_sh"])
    _verify_velero_node_agent(texts["velero_yaml"], texts["storage_sh"])
    _verify_nfs_quota_agent(root, texts["nqa_sh"])


def verify_helm_renders(root: Path) -> bool:
    falco_tgz = _find_chart(root, "falco-9.2.1.tgz")
    velero_tgz = _find_chart(root, "velero-12.0.3.tgz")
    nqa_tgz = _find_chart(root, "nfs-quota-agent-0.0.0-main.tgz")
    if not (falco_tgz and velero_tgz and nqa_tgz):
        return False

    # A. Falco offline render
    falco_vals = _app_values((root / FILES["falco_yaml"]).read_text())
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.dump(falco_vals, f)
        f.flush()
        out = subprocess.run(
            [
                "helm", "template", "falco", str(falco_tgz),
                "-n", "security-system", "-f", f.name,
                "--show-only", "templates/daemonset.yaml",
            ],
            capture_output=True, text=True, check=True,
        ).stdout
    falco_ds = yaml.safe_load(out)
    fpspec = falco_ds["spec"]["template"]["spec"]
    assert fpspec.get("securityContext") == {"seccompProfile": {"type": "RuntimeDefault"}}, (
        "falco render: pod seccompProfile.type must be RuntimeDefault"
    )
    fc = fpspec["containers"][0]
    assert fc["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {
            "drop": ["ALL"],
            "add": ["BPF", "SYS_RESOURCE", "PERFMON", "SYS_PTRACE"],
        },
    }, f"falco render: unexpected securityContext {fc['securityContext']}"
    assert any(m.get("mountPath") == "/root/.falco" for m in fc.get("volumeMounts") or []), (
        "falco render: /root/.falco volumeMount missing"
    )

    # B. Velero node-agent offline render (GitOps + 08-4)
    gitops_velero = _app_values((root / FILES["velero_yaml"]).read_text())
    boot_velero = _extract_velero_heredoc((root / FILES["storage_sh"]).read_text())
    for label, vals in (("gitops", gitops_velero), ("08-4", boot_velero)):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            yaml.dump(vals, f)
            f.flush()
            out = subprocess.run(
                [
                    "helm", "template", "velero", str(velero_tgz),
                    "-n", "storage", "-f", f.name,
                    "--show-only", "templates/node-agent-daemonset.yaml",
                ],
                capture_output=True, text=True, check=True,
            ).stdout
        nds = yaml.safe_load(out)
        nspec = nds["spec"]["template"]["spec"]
        assert nspec["securityContext"]["runAsNonRoot"] is False, f"{label} node-agent render runAsNonRoot"
        nc = nspec["containers"][0]
        assert nc["securityContext"] == {
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        }, f"{label} node-agent render securityContext: {nc['securityContext']}"
        vols = {v["name"]: v for v in nspec["volumes"]}
        assert vols.get("node-agent-home", {}).get("emptyDir") == {}, f"{label} node-agent render home vol"
        assert vols.get("tmp", {}).get("emptyDir") == {}, f"{label} node-agent render tmp vol"
        paths = [m["mountPath"] for m in nc["volumeMounts"]]
        assert paths.count("/root") == 1 and paths.count("/tmp") == 1, f"{label} node-agent mounts: {paths}"

    # C. nfs-quota-agent offline render
    nqa_args = _extract_nqa_helm_args((root / FILES["nqa_sh"]).read_text())
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["tar", "xzf", str(nqa_tgz), "-C", td], check=True)
        chart_dir = Path(td) / "nfs-quota-agent"
        out = subprocess.run(
            [
                "helm", "template", "nfs-quota-agent", str(chart_dir),
                *nqa_args, "--show-only", "templates/daemonset.yaml",
            ],
            capture_output=True, text=True, check=True,
        ).stdout
    nqa_ds = yaml.safe_load(out)
    nqa_pspec = nqa_ds["spec"]["template"]["spec"]
    assert (nqa_pspec.get("securityContext") or {}).get("seccompProfile", {}).get("type") == "RuntimeDefault", (
        "nfs-quota-agent render: pod seccompProfile.type must be RuntimeDefault"
    )
    nqa_c = nqa_pspec["containers"][0]
    assert nqa_c["securityContext"] == {"privileged": True, "readOnlyRootFilesystem": True}, (
        f"nfs-quota-agent render: unexpected container securityContext {nqa_c.get('securityContext')}"
    )
    return True


def mutation_verify(root: Path) -> int:
    sources = {k: (root / rel).read_text() for k, rel in FILES.items()}
    mutations = [
        (
            "falco_yaml",
            "            leastPrivileged: true",
            "            leastPrivileged: false",
        ),
        (
            "falco_yaml",
            "        containerSecurityContext:\n          allowPrivilegeEscalation: false\n          readOnlyRootFilesystem: true",
            "        containerSecurityContext:\n          allowPrivilegeEscalation: false\n          readOnlyRootFilesystem: false",
        ),
        (
            "velero_yaml",
            "          containerSecurityContext:\n            readOnlyRootFilesystem: true",
            "          containerSecurityContext:\n            readOnlyRootFilesystem: false",
        ),
        (
            "velero_yaml",
            "              drop: [\"ALL\"]",
            "              drop: [\"NET_RAW\"]",
        ),
        (
            "velero_yaml",
            "          containerSecurityContext:\n            readOnlyRootFilesystem: true",
            "          privileged: true\n          containerSecurityContext:\n            readOnlyRootFilesystem: true",
        ),
        ("velero_yaml", "mountPath: /root", "mountPath: /var/root"),
        (
            "velero_yaml",
            "            - name: tmp\n              mountPath: /tmp",
            "            - name: tmp\n              mountPath: /var/tmp",
        ),
        (
            "storage_sh",
            "  containerSecurityContext:\n    readOnlyRootFilesystem: true",
            "  containerSecurityContext:\n    readOnlyRootFilesystem: false",
        ),
        (
            "storage_sh",
            "      drop: [\"ALL\"]",
            "      drop: [\"NET_RAW\"]",
        ),
        (
            "storage_sh",
            "  containerSecurityContext:\n    readOnlyRootFilesystem: true",
            "  privileged: true\n  containerSecurityContext:\n    readOnlyRootFilesystem: true",
        ),
        ("storage_sh", "mountPath: /root", "mountPath: /var/root"),
        (
            "storage_sh",
            "    - name: node-agent-home\n      mountPath: /root\n    - name: tmp\n      mountPath: /tmp",
            "    - name: node-agent-home\n      mountPath: /root\n    - name: tmp\n      mountPath: /var/tmp",
        ),
        ("nqa_sh", "  --set podSecurityContext.seccompProfile.type=RuntimeDefault\n", ""),
        ("nqa_sh", "  --set securityContext.privileged=true\n", ""),
        ("nqa_sh", "--set securityContext.readOnlyRootFilesystem=true", "--set securityContext.readOnlyRootFilesystem=false"),
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
        print(f"PASS: R371 round-4 compliance hardening + {renders} ({caught} mutations rejected)")
    else:
        print(f"PASS: R371 round-4 compliance hardening + {renders}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
