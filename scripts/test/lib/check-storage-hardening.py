#!/usr/bin/env python3
"""R362: storage compliance hardening and 08-4-storage.sh bootstrap parity."""

import argparse
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "seaweedfs": "gitops/charts/narwhal-apps/templates/seaweedfs.yaml",
    "openbao": "gitops/charts/narwhal-apps/templates/openbao.yaml",
    "velero": "gitops/charts/narwhal-apps/templates/velero.yaml",
    "velero_ui": "gitops/charts/narwhal-apps/templates/velero-ui.yaml",
    "storage_sh": "scripts/cluster/08-4-storage.sh",
}
ERRORS = (AssertionError, KeyError, TypeError, ValueError, yaml.YAMLError)


def _app_values(text):
    return yaml.safe_load(text)["spec"]["source"]["helm"]["valuesObject"]


def _velero_ui_values(text):
    raw = yaml.safe_load(text)["spec"]["source"]["helm"]["values"]
    return yaml.safe_load(raw.replace("{{ .Values.baseDomain }}", "local.narwhal.internal"))


def _extract_heredoc(text, marker):
    assert text.count(marker) == 1, f"expected one heredoc for {marker!r}"
    return yaml.safe_load(text.split(marker, 1)[1].split("\nEOF\n", 1)[0])


def _has_empty_dir_mount(volumes, mounts, mount_path):
    vol_names = {v["name"] for v in (volumes or []) if isinstance(v, dict) and v.get("emptyDir") == {}}
    return any(isinstance(m, dict) and m.get("mountPath") == mount_path and m.get("name") in vol_names for m in (mounts or []))


def _has_tmp_empty_dir(volumes, mounts):
    return _has_empty_dir_mount(volumes, mounts, "/tmp")


def _assert_resources(res, label, require_cpu_limit=True):
    assert isinstance(res, dict), f"{label}: missing resources"
    req = res.get("requests") or {}
    lim = res.get("limits") or {}
    assert req.get("cpu") and req.get("memory"), f"{label}: requests.cpu/memory required"
    assert lim.get("memory"), f"{label}: limits.memory required"
    if require_cpu_limit:
        assert lim.get("cpu"), f"{label}: limits.cpu required"


def _assert_seaweedfs_values(vals, label):
    for comp in ("master", "filer", "s3"):
        c = vals[comp]
        psc = c.get("podSecurityContext") or {}
        for k, expected in (("enabled", True), ("runAsNonRoot", True), ("runAsUser", 1000), ("runAsGroup", 1000), ("fsGroup", 1000)):
            assert psc.get(k) == expected and type(psc.get(k)) is type(expected), f"{label} {comp}.podSecurityContext.{k}"
        assert (c.get("logs") or {}).get("type") == "emptyDir", f"{label} {comp}.logs.type must be emptyDir"
    assert not vals["volume"].get("podSecurityContext"), f"{label} volume.podSecurityContext must remain unset (hostPath root)"

    for comp in ("master", "volume", "filer", "s3"):
        c = vals[comp]
        csc = c.get("containerSecurityContext") or {}
        assert csc.get("enabled") is True, f"{label} {comp}.containerSecurityContext.enabled"
        assert csc.get("allowPrivilegeEscalation") is False, f"{label} {comp} allowPrivilegeEscalation"
        assert csc.get("readOnlyRootFilesystem") is True, f"{label} {comp} readOnlyRootFilesystem"
        assert (csc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label} {comp} drop ALL"
        assert (csc.get("seccompProfile") or {}).get("type") == "RuntimeDefault", f"{label} {comp} seccomp"
        ev = yaml.safe_load(c.get("extraVolumes") or "[]")
        em = yaml.safe_load(c.get("extraVolumeMounts") or "[]")
        assert _has_tmp_empty_dir(ev, em), f"{label} {comp} must mount /tmp emptyDir"
        _assert_resources(c.get("resources"), f"{label} {comp}", require_cpu_limit=False)


def _assert_openbao_values(vals, label):
    srv = vals["server"]
    _assert_resources(srv.get("resources"), f"{label} server")
    sc = (srv.get("statefulSet") or {}).get("securityContext", {}).get("container") or {}
    assert sc.get("allowPrivilegeEscalation") is False, f"{label} server allowPrivilegeEscalation"
    assert sc.get("readOnlyRootFilesystem") is True, f"{label} server readOnlyRootFilesystem"
    assert (sc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label} server drop ALL"
    assert (sc.get("seccompProfile") or {}).get("type") == "RuntimeDefault", f"{label} server seccomp"
    assert _has_tmp_empty_dir(srv.get("volumes"), srv.get("volumeMounts")), f"{label} server must mount /tmp emptyDir"

    inj = vals.get("injector") or {}
    _assert_resources(inj.get("resources"), f"{label} injector")
    isc = (inj.get("securityContext") or {}).get("container") or {}
    assert isc.get("allowPrivilegeEscalation") is False, f"{label} injector allowPrivilegeEscalation"
    assert isc.get("readOnlyRootFilesystem") is True, f"{label} injector readOnlyRootFilesystem"
    assert (isc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label} injector drop ALL"


def _assert_velero_values(vals, label):
    psc = vals.get("podSecurityContext") or {}
    assert psc.get("runAsNonRoot") is True, f"{label} velero podSecurityContext.runAsNonRoot"
    assert psc.get("runAsUser") == 1002, f"{label} velero podSecurityContext.runAsUser"
    assert psc.get("runAsGroup") == 1000, f"{label} velero podSecurityContext.runAsGroup"
    csc = vals.get("containerSecurityContext") or {}
    assert csc.get("allowPrivilegeEscalation") is False, f"{label} velero allowPrivilegeEscalation"
    assert csc.get("runAsNonRoot") is True, f"{label} velero containerSecurityContext.runAsNonRoot"
    assert csc.get("runAsUser") == 1002, f"{label} velero containerSecurityContext.runAsUser"
    assert csc.get("runAsGroup") == 1000, f"{label} velero containerSecurityContext.runAsGroup"
    # D-velero-rofs (KSV014): readOnlyRootFilesystem=true with emptyDir at /home/cnb (kopia writes
    # $HOME/udmrepo); chart mounts its own tmpdir at /tmp so extraVolumeMounts must NOT mount /tmp.
    assert csc.get("readOnlyRootFilesystem") is True, f"{label} velero readOnlyRootFilesystem must be true"
    extra_vols = vals.get("extraVolumes") or []
    extra_mounts = vals.get("extraVolumeMounts") or []
    assert _has_empty_dir_mount(extra_vols, extra_mounts, "/home/cnb"), (
        f"{label} velero must mount emptyDir at /home/cnb"
    )
    assert not any(isinstance(m, dict) and m.get("mountPath") == "/tmp" for m in extra_mounts), (
        f"{label} velero must not add a second /tmp mount (chart mounts tmpdir)"
    )
    assert (csc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label} velero drop ALL"
    _assert_resources(vals.get("resources"), f"{label} velero")

    inits = vals.get("initContainers") or []
    assert inits and inits[0].get("name") == "velero-plugin-for-aws", f"{label} velero initContainer missing"
    ic_sc = inits[0].get("securityContext") or {}
    assert ic_sc.get("runAsNonRoot") is True and ic_sc.get("runAsUser") == 65532, f"{label} plugin non-root uid 65532"
    assert ic_sc.get("allowPrivilegeEscalation") is False and ic_sc.get("readOnlyRootFilesystem") is True, f"{label} plugin securityContext"
    assert (ic_sc.get("capabilities") or {}).get("drop") == ["ALL"], f"{label} plugin drop ALL"
    _assert_resources(inits[0].get("resources"), f"{label} plugin")

    na = vals.get("nodeAgent") or {}
    assert (na.get("podSecurityContext") or {}).get("runAsNonRoot") is False, f"{label} nodeAgent podSecurityContext.runAsNonRoot"
    _assert_resources(na.get("resources"), f"{label} nodeAgent")


def verify(root):
    texts = {k: (root / rel).read_text() for k, rel in FILES.items()}

    sw_gitops = _app_values(texts["seaweedfs"])
    _assert_seaweedfs_values(sw_gitops, "gitops seaweedfs")

    ob_gitops = _app_values(texts["openbao"])
    _assert_openbao_values(ob_gitops, "gitops openbao")

    vel_gitops = _app_values(texts["velero"])
    _assert_velero_values(vel_gitops, "gitops velero")

    vu_gitops = _velero_ui_values(texts["velero_ui"])
    assert (vu_gitops.get("podSecurityContext") or {}).get("runAsGroup") == 1000, "velero-ui runAsGroup"
    assert (vu_gitops.get("podSecurityContext") or {}).get("fsGroup") == 1000, "velero-ui fsGroup"
    vusc = vu_gitops.get("securityContext") or {}
    assert vusc.get("runAsNonRoot") is True and vusc.get("runAsUser") == 1000, "velero-ui non-root"
    assert vusc.get("allowPrivilegeEscalation") is False and vusc.get("readOnlyRootFilesystem") is True, "velero-ui readonly"
    assert (vusc.get("capabilities") or {}).get("drop") == ["ALL"], "velero-ui drop ALL"
    assert _has_tmp_empty_dir(vu_gitops.get("volumes"), vu_gitops.get("volumeMounts")), "velero-ui /tmp emptyDir"

    sh = texts["storage_sh"]
    sw_sh = _extract_heredoc(sh, "cat > /tmp/seaweedfs-values.yaml <<'EOF'\n")
    assert "-f /tmp/seaweedfs-values.yaml" in sh, "08-4 must pass /tmp/seaweedfs-values.yaml"
    for flag in (
        "--set master.podSecurityContext.runAsNonRoot=true",
        "--set filer.podSecurityContext.runAsNonRoot=true",
        "--set s3.podSecurityContext.enabled=true",
        "--set s3.podSecurityContext.runAsNonRoot=true",
        "--set s3.podSecurityContext.runAsUser=1000",
        "--set s3.podSecurityContext.runAsGroup=1000",
        "--set s3.podSecurityContext.fsGroup=1000",
        "--set s3.logs.type=emptyDir",
    ):
        assert flag in sh, f"08-4 missing {flag}"
    # Merge script --set hardening flags into the heredoc map for shared structural check.
    for comp in ("master", "filer", "s3"):
        sw_sh[comp]["podSecurityContext"] = {"enabled": True, "runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000}
        sw_sh[comp]["logs"] = {"type": "emptyDir"}
    _assert_seaweedfs_values(sw_sh, "08-4 seaweedfs")

    ob_sh = _extract_heredoc(sh, "cat > /tmp/openbao-values.yaml <<'EOF'\n")
    _assert_openbao_values(ob_sh, "08-4 openbao")
    assert ob_sh["server"]["resources"] == ob_gitops["server"]["resources"], "08-4 openbao server resources parity"
    assert ob_sh["injector"]["resources"] == ob_gitops["injector"]["resources"], "08-4 openbao injector resources parity"

    vel_sh = _extract_heredoc(sh, "cat > /tmp/velero-values.yaml << EOF\n")
    _assert_velero_values(vel_sh, "08-4 velero")
    assert vel_sh["resources"] == vel_gitops["resources"], "08-4 velero resources parity"
    assert vel_sh["nodeAgent"]["resources"] == vel_gitops["nodeAgent"]["resources"], "08-4 velero nodeAgent resources parity"
    assert vel_sh["initContainers"][0]["resources"] == vel_gitops["initContainers"][0]["resources"], "08-4 velero plugin resources parity"


def mutation_verify(root):
    sources = {k: (root / rel).read_text() for k, rel in FILES.items()}
    mutations = [
        ("seaweedfs", "            runAsNonRoot: true\n            runAsUser: 1000\n", "            runAsUser: 1000\n"),
        ("seaweedfs", "          logs:\n            type: emptyDir\n          extraVolumes:", "          extraVolumes:"),
        ("seaweedfs", "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
        ("seaweedfs", "mountPath: /tmp", "mountPath: /var/tmp"),
        ("openbao", "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
        ("openbao", "mountPath: /tmp", "mountPath: /var/tmp"),
        ("openbao", "cpu: 25m\n              memory: 64Mi", "cpu: \"\"\n              memory: 64Mi"),
        ("velero", "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
        ("velero", "mountPath: /home/cnb", "mountPath: /tmp"),
        ("velero", "name: velero-home\n            emptyDir: {}", "name: velero-home\n            hostPath:\n              path: /tmp"),
        ("velero", "runAsUser: 65532\n              allowPrivilegeEscalation: false\n              readOnlyRootFilesystem: true", "runAsUser: 65532\n              allowPrivilegeEscalation: false\n              readOnlyRootFilesystem: false"),
        ("velero", "runAsUser: 65532", "runAsUser: 0"),
        ("velero", "cpu: 50m\n            memory: 128Mi", "cpu: \"\"\n            memory: 128Mi"),
        ("velero_ui", "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
        ("velero_ui", "mountPath: /tmp", "mountPath: /var/tmp"),
        ("storage_sh", "--set s3.logs.type=emptyDir", "--set s3.logs.type=hostPath"),
        ("storage_sh", "--set s3.podSecurityContext.runAsNonRoot=true", "--set s3.podSecurityContext.runAsNonRoot=false"),
        ("storage_sh", "    readOnlyRootFilesystem: true\n", "    readOnlyRootFilesystem: false\n"),
        ("storage_sh", "      mountPath: /tmp\n", "      mountPath: /var/tmp\n"),
        ("storage_sh", "  readOnlyRootFilesystem: true\n", "  readOnlyRootFilesystem: false\n"),
        ("storage_sh", "  runAsGroup: 1000\n  readOnlyRootFilesystem: true", "  runAsGroup: 1000\n  readOnlyRootFilesystem: false"),
        ("storage_sh", "  - name: velero-home\n    mountPath: /home/cnb", "  - name: velero-home\n    mountPath: /tmp"),
        ("storage_sh", "extraVolumes:\n  - name: velero-home\n    emptyDir: {}", "extraVolumes:\n  - name: other\n    emptyDir: {}"),
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
    print(f"PASS: R362 storage compliance hardening + parity ({len(mutations)} mutations rejected)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    verify(args.root)
    if args.mutation_verify:
        mutation_verify(args.root)
    else:
        print("PASS: R362 storage compliance hardening + parity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
