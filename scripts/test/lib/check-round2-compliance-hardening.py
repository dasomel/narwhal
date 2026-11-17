#!/usr/bin/env python3
"""R366: Workload compliance hardening round 2 (Gitea, Tempo, APISIX-IC, Velero).

Pins:
1. devtools/gitea (12-gitea.sh): containerSecurityContext runAsNonRoot=true, runAsUser=1000,
   runAsGroup=1000 across all 4 containers; readOnlyRootFilesystem remains unset.
2. monitoring/tempo (tempo.yaml & 08-2-monitoring.sh): tempo.securityContext
   allowPrivilegeEscalation=false, readOnlyRootFilesystem=true, drop ALL; pod seccomp
   RuntimeDefault; GitOps mounts a /tmp emptyDir next to the chart's /var/tempo storage claim (persistence=true, same as 08-2) and
   08-2 mounts /tmp emptyDir alongside PVC /var/tempo (persistence=true).
3. platform-system/apisix-ingress-controller (08-1-networking.sh): runAsNonRoot=true,
   runAsUser=65532, runAsGroup=65532, readOnlyRootFilesystem=true on both
   wait-apisix-admin and apisix-ingress-controller containers.
4. storage/velero (velero.yaml & 08-4-storage.sh): pod & container runAsNonRoot=true,
   runAsUser=1002, runAsGroup=1000 (real cnb UID/GID), no fsGroup (keeps /target writable
   for velero-plugin-for-aws UID 65532), no readOnlyRootFilesystem, nodeAgent untouched.
5. Offline helm template renders when narwhal-airgap-bundle-*/charts is present.
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
    "gitea_sh": "scripts/cluster/12-gitea.sh",
    "tempo_yaml": "gitops/charts/narwhal-apps/templates/tempo.yaml",
    "mon_sh": "scripts/cluster/08-2-monitoring.sh",
    "net_sh": "scripts/cluster/08-1-networking.sh",
    "velero_yaml": "gitops/charts/narwhal-apps/templates/velero.yaml",
    "storage_sh": "scripts/cluster/08-4-storage.sh",
}
ERRORS = (AssertionError, KeyError, TypeError, ValueError, yaml.YAMLError)


def _app_values(text: str) -> dict:
    cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", text)
    return yaml.safe_load(cleaned)["spec"]["source"]["helm"]["valuesObject"]


def _extract_velero_heredoc(text: str) -> dict:
    marker = "cat > /tmp/velero-values.yaml << EOF\n"
    assert text.count(marker) == 1, "08-4-storage.sh: expected one velero values heredoc"
    return yaml.safe_load(text.split(marker, 1)[1].split("\nEOF\n", 1)[0])


def _active_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _verify_gitea(gitea_sh: str) -> None:
    active = _active_lines(gitea_sh)
    for flag in (
        "--set containerSecurityContext.allowPrivilegeEscalation=false",
        "--set containerSecurityContext.capabilities.drop[0]=ALL",
        "--set containerSecurityContext.seccompProfile.type=RuntimeDefault",
        "--set containerSecurityContext.runAsNonRoot=true",
        "--set containerSecurityContext.runAsUser=1000",
        "--set containerSecurityContext.runAsGroup=1000",
    ):
        assert flag in active, f"12-gitea.sh missing {flag}"
    assert "containerSecurityContext.readOnlyRootFilesystem" not in active, (
        "12-gitea.sh must not set readOnlyRootFilesystem on gitea"
    )


def _verify_tempo(tempo_yaml: str, mon_sh: str) -> None:
    vals = _app_values(tempo_yaml)
    tsc = (vals.get("tempo") or {}).get("securityContext") or {}
    assert tsc.get("allowPrivilegeEscalation") is False, "tempo.yaml: allowPrivilegeEscalation must be false"
    assert tsc.get("readOnlyRootFilesystem") is True, "tempo.yaml: readOnlyRootFilesystem must be true"
    assert (tsc.get("capabilities") or {}).get("drop") == ["ALL"], "tempo.yaml: capabilities.drop must be ['ALL']"
    psc = vals.get("securityContext") or {}
    assert (psc.get("seccompProfile") or {}).get("type") == "RuntimeDefault", "tempo.yaml: pod seccompProfile missing"

    pers = vals.get("persistence") or {}
    assert pers.get("enabled") is True and pers.get("storageClassName") == "nfs-csi" and pers.get("size") == "10Gi", (
        "tempo.yaml: persistence must match the bootstrap install (enabled, nfs-csi, 10Gi)"
    )
    vols = {v["name"]: v for v in (vals.get("extraVolumes") or []) if isinstance(v, dict)}
    mounts = {m["mountPath"]: m["name"] for m in ((vals.get("tempo") or {}).get("extraVolumeMounts") or []) if isinstance(m, dict)}
    # The chart mounts its own `storage` volume at /var/tempo; a second mount there is rejected
    # by the API server (duplicate mountPath), so GitOps must add /tmp only.
    assert "/var/tempo" not in mounts, "tempo.yaml: must not duplicate the chart's /var/tempo mount"
    for mp in ("/tmp",):
        assert mp in mounts, f"tempo.yaml: missing extraVolumeMount {mp}"
        vname = mounts[mp]
        assert vols.get(vname, {}).get("emptyDir") == {}, f"tempo.yaml: volume {vname} for {mp} must be emptyDir"

    active_mon = _active_lines(mon_sh)
    tempo_start = active_mon.index("helm upgrade --install tempo")
    tempo_end = active_mon.index("TEMPO_OK=true", tempo_start)
    tempo_cmd = active_mon[tempo_start:tempo_end]
    for flag in (
        "--set tempo.securityContext.allowPrivilegeEscalation=false",
        "--set tempo.securityContext.readOnlyRootFilesystem=true",
        "--set 'tempo.securityContext.capabilities.drop[0]=ALL'",
        "--set securityContext.seccompProfile.type=RuntimeDefault",
        "--set extraVolumes[0].name=tmp",
        "--set-json 'extraVolumes[0].emptyDir={}'",
        "--set tempo.extraVolumeMounts[0].name=tmp",
        "--set tempo.extraVolumeMounts[0].mountPath=/tmp",
    ):
        assert flag in tempo_cmd, f"08-2-monitoring.sh tempo block missing {flag}"
    assert "mountPath=/var/tempo" not in tempo_cmd, (
        "08-2-monitoring.sh uses persistence.enabled=true so must not duplicate /var/tempo mount"
    )


def _verify_apisix_ic(net_sh: str) -> None:
    active = _active_lines(net_sh)
    ic_start = active.index("helm upgrade --install apisix-ingress-controller")
    ic_end = active.index("APISIX_INGRESS_CONTROLLER_OK=true", ic_start)
    ic_cmd = active[ic_start:ic_end]
    for flag in (
        "--set securityContext.allowPrivilegeEscalation=false",
        "--set 'securityContext.capabilities.drop[0]=ALL'",
        "--set securityContext.seccompProfile.type=RuntimeDefault",
        "--set securityContext.runAsNonRoot=true",
        "--set securityContext.runAsUser=65532",
        "--set securityContext.runAsGroup=65532",
        "--set securityContext.readOnlyRootFilesystem=true",
    ):
        assert flag in ic_cmd, f"08-1-networking.sh apisix-ic block missing {flag}"


def _verify_velero(velero_yaml: str, storage_sh: str) -> None:
    gitops_vals = _app_values(velero_yaml)
    boot_vals = _extract_velero_heredoc(storage_sh)
    for label, vals in (("gitops", gitops_vals), ("08-4", boot_vals)):
        psc = vals.get("podSecurityContext") or {}
        assert psc.get("runAsNonRoot") is True, f"{label} velero podSecurityContext.runAsNonRoot"
        assert psc.get("runAsUser") == 1002, f"{label} velero podSecurityContext.runAsUser"
        assert psc.get("runAsGroup") == 1000, f"{label} velero podSecurityContext.runAsGroup"
        assert "fsGroup" not in psc, f"{label} velero podSecurityContext must omit fsGroup (plugin uid 65532)"

        csc = vals.get("containerSecurityContext") or {}
        assert csc.get("allowPrivilegeEscalation") is False, f"{label} velero allowPrivilegeEscalation"
        assert csc.get("runAsNonRoot") is True, f"{label} velero containerSecurityContext.runAsNonRoot"
        assert csc.get("runAsUser") == 1002, f"{label} velero containerSecurityContext.runAsUser"
        assert csc.get("runAsGroup") == 1000, f"{label} velero containerSecurityContext.runAsGroup"
        assert csc.get("readOnlyRootFilesystem") is not True, f"{label} velero must not set readOnlyRootFilesystem"
        assert (csc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label} velero drop ALL"

        na_psc = (vals.get("nodeAgent") or {}).get("podSecurityContext") or {}
        assert na_psc.get("runAsNonRoot") is False, f"{label} nodeAgent must remain runAsNonRoot: false"


def verify(root: Path) -> None:
    texts = {k: (root / rel).read_text() for k, rel in FILES.items()}
    _verify_gitea(texts["gitea_sh"])
    _verify_tempo(texts["tempo_yaml"], texts["mon_sh"])
    _verify_apisix_ic(texts["net_sh"])
    _verify_velero(texts["velero_yaml"], texts["storage_sh"])


def verify_helm_renders(root: Path) -> None:
    bundles = sorted(glob.glob(str(root / "narwhal-airgap-bundle-*/charts/tempo-2.2.3.tgz")))
    if not bundles:
        bundles = sorted(glob.glob(str(root.parents[2] / "narwhal-airgap-bundle-*/charts/tempo-2.2.3.tgz")))
    if not bundles:
        return
    charts_dir = Path(bundles[0]).parent

    # 1. Gitea offline render
    out = subprocess.run(
        [
            "helm", "template", "gitea", str(charts_dir / "gitea-12.6.0.tgz"),
            "--namespace", "devtools",
            "--set", "image.tag=1.26.2",
            "--set", "postgresql.enabled=false",
            "--set", "postgresql-ha.enabled=false",
            "--set", "redis-cluster.enabled=false",
            "--set", "redis.enabled=false",
            "--set", "valkey-cluster.enabled=false",
            "--set", "valkey.enabled=false",
            "--set", "containerSecurityContext.allowPrivilegeEscalation=false",
            "--set", "containerSecurityContext.capabilities.drop[0]=ALL",
            "--set", "containerSecurityContext.seccompProfile.type=RuntimeDefault",
            "--set", "containerSecurityContext.runAsNonRoot=true",
            "--set", "containerSecurityContext.runAsUser=1000",
            "--set", "containerSecurityContext.runAsGroup=1000",
            "--show-only", "templates/gitea/deployment.yaml",
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    gitea_dep = yaml.safe_load(out)
    pod_spec = gitea_dep["spec"]["template"]["spec"]
    assert pod_spec["securityContext"]["fsGroup"] == 1000
    for c in pod_spec["initContainers"] + pod_spec["containers"]:
        sc = c["securityContext"]
        assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 1000 and sc["runAsGroup"] == 1000, c["name"]
        assert "readOnlyRootFilesystem" not in sc, c["name"]

    # 2. Tempo offline render (GitOps)
    tempo_vals = _app_values((root / FILES["tempo_yaml"]).read_text())
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.dump(tempo_vals, f)
        f.flush()
        out = subprocess.run(
            ["helm", "template", "tempo", str(charts_dir / "tempo-2.2.3.tgz"), "-n", "monitoring", "-f", f.name, "--show-only", "templates/statefulset.yaml"],
            capture_output=True, text=True, check=True,
        ).stdout
    tempo_sts = yaml.safe_load(out)
    tspec = tempo_sts["spec"]["template"]["spec"]
    tc = tspec["containers"][0]
    assert tc["securityContext"]["allowPrivilegeEscalation"] is False
    assert tc["securityContext"]["readOnlyRootFilesystem"] is True
    assert tc["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert tspec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert tspec["securityContext"]["runAsNonRoot"] is True
    gitops_paths = [m["mountPath"] for m in tc["volumeMounts"]]
    assert gitops_paths.count("/var/tempo") == 1 and gitops_paths.count("/tmp") == 1, gitops_paths

    # 2b. Tempo offline render (08-2 bootstrap parity with persistence=true)
    out = subprocess.run(
        [
            "helm", "template", "tempo", str(charts_dir / "tempo-2.2.3.tgz"),
            "-n", "monitoring",
            "--set", "tempo.tag=2.9.0",
            "--set", "tempo.storage.trace.backend=local",
            "--set", "persistence.enabled=true",
            "--set", "persistence.storageClassName=nfs-csi",
            "--set", "persistence.size=10Gi",
            "--set", "tempo.securityContext.allowPrivilegeEscalation=false",
            "--set", "tempo.securityContext.readOnlyRootFilesystem=true",
            "--set", "tempo.securityContext.capabilities.drop[0]=ALL",
            "--set", "securityContext.seccompProfile.type=RuntimeDefault",
            "--set", "extraVolumes[0].name=tmp",
            "--set-json", "extraVolumes[0].emptyDir={}",
            "--set", "tempo.extraVolumeMounts[0].name=tmp",
            "--set", "tempo.extraVolumeMounts[0].mountPath=/tmp",
            "--show-only", "templates/statefulset.yaml",
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    tempo_boot_sts = yaml.safe_load(out)
    btc = tempo_boot_sts["spec"]["template"]["spec"]["containers"][0]
    assert btc["securityContext"]["readOnlyRootFilesystem"] is True
    assert btc["securityContext"]["allowPrivilegeEscalation"] is False
    mount_paths = [m["mountPath"] for m in btc["volumeMounts"]]
    assert mount_paths.count("/var/tempo") == 1 and mount_paths.count("/tmp") == 1

    # 3. APISIX Ingress Controller offline render
    out = subprocess.run(
        [
            "helm", "template", "apisix-ingress-controller", str(charts_dir / "apisix-ingress-controller-0.14.1.tgz"),
            "-n", "platform-system",
            "--set", "config.etcdserver.enabled=false",
            "--set", "securityContext.allowPrivilegeEscalation=false",
            "--set", "securityContext.capabilities.drop[0]=ALL",
            "--set", "securityContext.seccompProfile.type=RuntimeDefault",
            "--set", "securityContext.runAsNonRoot=true",
            "--set", "securityContext.runAsUser=65532",
            "--set", "securityContext.runAsGroup=65532",
            "--set", "securityContext.readOnlyRootFilesystem=true",
            "--show-only", "templates/deployment.yaml",
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    aic_dep = yaml.safe_load(out)
    aspec = aic_dep["spec"]["template"]["spec"]
    for c in aspec["initContainers"] + aspec["containers"]:
        sc = c["securityContext"]
        assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 65532 and sc["runAsGroup"] == 65532
        assert sc["readOnlyRootFilesystem"] is True

    # 4. Velero offline render
    velero_vals = _app_values((root / FILES["velero_yaml"]).read_text())
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.dump(velero_vals, f)
        f.flush()
        out = subprocess.run(
            ["helm", "template", "velero", str(charts_dir / "velero-12.0.3.tgz"), "-n", "storage", "-f", f.name, "--show-only", "templates/deployment.yaml"],
            capture_output=True, text=True, check=True,
        ).stdout
    vdep = yaml.safe_load(out)
    vspec = vdep["spec"]["template"]["spec"]
    assert vspec["securityContext"] == {"runAsNonRoot": True, "runAsUser": 1002, "runAsGroup": 1000}
    vc = vspec["containers"][0]
    assert vc["securityContext"]["runAsNonRoot"] is True
    assert vc["securityContext"]["runAsUser"] == 1002
    assert vc["securityContext"]["runAsGroup"] == 1000
    assert "readOnlyRootFilesystem" not in vc["securityContext"]


def mutation_verify(root: Path) -> int:
    sources = {k: (root / rel).read_text() for k, rel in FILES.items()}
    mutations = [
        ("gitea_sh", "--set containerSecurityContext.runAsNonRoot=true", "--set containerSecurityContext.runAsNonRoot=false"),
        ("gitea_sh", "--set containerSecurityContext.runAsUser=1000", "--set containerSecurityContext.runAsUser=0"),
        ("gitea_sh", "--set containerSecurityContext.runAsGroup=1000", "--set containerSecurityContext.runAsGroup=0"),
        ("tempo_yaml", "allowPrivilegeEscalation: false", "allowPrivilegeEscalation: true"),
        ("tempo_yaml", "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
        ("tempo_yaml", "mountPath: /tmp", "mountPath: /var/tmp"),
        ("mon_sh", "--set tempo.securityContext.readOnlyRootFilesystem=true", "--set tempo.securityContext.readOnlyRootFilesystem=false"),
        ("mon_sh", "--set tempo.extraVolumeMounts[0].mountPath=/tmp", "--set tempo.extraVolumeMounts[0].mountPath=/var/tmp"),
        ("net_sh", "--set securityContext.runAsGroup=65532", "--set securityContext.runAsGroup=0"),
        ("net_sh", "--set securityContext.readOnlyRootFilesystem=true", "--set securityContext.readOnlyRootFilesystem=false"),
        ("velero_yaml", "runAsUser: 1002", "runAsUser: 0"),
        ("velero_yaml", "runAsNonRoot: true\n          runAsUser: 1002", "runAsNonRoot: false\n          runAsUser: 1002"),
        ("storage_sh", "runAsUser: 1002\n  runAsGroup: 1000", "runAsUser: 1000\n  runAsGroup: 1000"),
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
    verify_helm_renders(args.root)
    if args.mutation_verify:
        caught = mutation_verify(args.root)
        print(f"PASS: R366 round-2 compliance hardening + offline helm renders ({caught} mutations rejected)")
    else:
        print("PASS: R366 round-2 compliance hardening + offline helm renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
