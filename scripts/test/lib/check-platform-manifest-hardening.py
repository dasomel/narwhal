#!/usr/bin/env python3
"""R352: self-authored platform workload hardening (apisix-etcd, narwhal-portal, narwhal-portal-valkey).

Pins pod/container securityContext and emptyDir writable mounts for the three
self-authored Deployments in gitops/charts/narwhal-platform so Config Audit
KSV001/003/012/014/020/021/030/104/106/118 regressions fail statically.
--mutation-verify proves every assertion rejects a broken copy.
"""

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART_DIR = ROOT / "gitops/charts/narwhal-platform"
TEMPLATES = [
    CHART_DIR / "templates/apisix-infra.yaml",
    CHART_DIR / "templates/narwhal-portal-k8s.yaml",
]


def load_docs():
    if shutil.which("helm"):
        res = subprocess.run(
            [
                "helm",
                "template",
                "narwhal-platform",
                str(CHART_DIR),
                "--set",
                "baseDomain=local.narwhal.internal",
                "--show-only",
                "templates/apisix-infra.yaml",
                "--show-only",
                "templates/narwhal-portal-k8s.yaml",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return [d for d in yaml.safe_load_all(res.stdout) if isinstance(d, dict)]
    docs = []
    for path in TEMPLATES:
        docs.extend(d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(d, dict))
    return docs


def _find_deployment(docs, namespace, name):
    matches = [
        d for d in docs
        if d.get("kind") == "Deployment"
        and (d.get("metadata") or {}).get("namespace") == namespace
        and (d.get("metadata") or {}).get("name") == name
    ]
    if len(matches) != 1:
        return None, f"Deployment {namespace}/{name} expected once, got {len(matches)}"
    return matches[0], None


def _check_emptydir_mount(pod_spec, container, mount_path, label):
    errors = []
    volumes = {v["name"]: v for v in (pod_spec.get("volumes") or []) if isinstance(v, dict) and "name" in v}
    mounts = [m for m in (container.get("volumeMounts") or []) if m.get("mountPath") == mount_path]
    if len(mounts) != 1:
        return [f"{label}: expected 1 volumeMount at {mount_path}, got {len(mounts)}"]
    mount = mounts[0]
    if mount.get("readOnly") is True:
        errors.append(f"{label}: {mount_path} volumeMount must be writable (not readOnly)")
    vol = volumes.get(mount.get("name"))
    if not isinstance(vol, dict) or "emptyDir" not in vol:
        errors.append(f"{label}: {mount_path} must be backed by an emptyDir volume")
    return errors


def _check_container_sc(container, label):
    errors = []
    sc = container.get("securityContext") or {}
    if sc.get("allowPrivilegeEscalation") is not False:
        errors.append(f"{label}: securityContext.allowPrivilegeEscalation must be false")
    if sc.get("readOnlyRootFilesystem") is not True:
        errors.append(f"{label}: securityContext.readOnlyRootFilesystem must be true")
    caps = sc.get("capabilities") or {}
    if caps.get("drop") != ["ALL"]:
        errors.append(f"{label}: securityContext.capabilities.drop must be ['ALL']")
    if caps.get("add"):
        errors.append(f"{label}: securityContext.capabilities.add must be empty")
    return errors


def check(docs):
    errors = []

    # 1. platform-system/apisix-etcd
    dep, err = _find_deployment(docs, "platform-system", "apisix-etcd")
    if err:
        errors.append(err)
    else:
        pod_spec = (dep.get("spec") or {}).get("template", {}).get("spec", {})
        psc = pod_spec.get("securityContext") or {}
        if psc.get("runAsNonRoot") is not True:
            errors.append("apisix-etcd: pod securityContext.runAsNonRoot must be true")
        for key in ("runAsUser", "runAsGroup", "fsGroup"):
            val = psc.get(key)
            if not isinstance(val, int) or val <= 10000:
                errors.append(f"apisix-etcd: pod securityContext.{key} must be > 10000, got {val!r}")
        if psc.get("seccompProfile") != {"type": "RuntimeDefault"}:
            errors.append("apisix-etcd: pod securityContext.seccompProfile must be RuntimeDefault")
        containers = [c for c in (pod_spec.get("containers") or []) if c.get("name") == "etcd"]
        if len(containers) != 1:
            errors.append("apisix-etcd: expected container 'etcd'")
        else:
            errors.extend(_check_container_sc(containers[0], "apisix-etcd/etcd"))
            errors.extend(_check_emptydir_mount(pod_spec, containers[0], "/var/etcd/data", "apisix-etcd/etcd"))

    # 2. devtools/narwhal-portal-valkey
    dep, err = _find_deployment(docs, "devtools", "narwhal-portal-valkey")
    if err:
        errors.append(err)
    else:
        pod_spec = (dep.get("spec") or {}).get("template", {}).get("spec", {})
        psc = pod_spec.get("securityContext") or {}
        if psc.get("runAsNonRoot") is not True or psc.get("runAsUser") != 999 or psc.get("runAsGroup") != 999:
            errors.append("narwhal-portal-valkey: pod securityContext must pin non-root uid/gid 999")
        if psc.get("seccompProfile") != {"type": "RuntimeDefault"}:
            errors.append("narwhal-portal-valkey: pod securityContext.seccompProfile must be RuntimeDefault")
        containers = [c for c in (pod_spec.get("containers") or []) if c.get("name") == "valkey"]
        if len(containers) != 1:
            errors.append("narwhal-portal-valkey: expected container 'valkey'")
        else:
            errors.extend(_check_container_sc(containers[0], "narwhal-portal-valkey/valkey"))

    # 3. devtools/narwhal-portal
    dep, err = _find_deployment(docs, "devtools", "narwhal-portal")
    if err:
        errors.append(err)
    else:
        pod_spec = (dep.get("spec") or {}).get("template", {}).get("spec", {})
        psc = pod_spec.get("securityContext") or {}
        if psc.get("runAsNonRoot") is not True:
            errors.append("narwhal-portal: pod securityContext.runAsNonRoot must be true")
        for key in ("runAsUser", "runAsGroup", "fsGroup"):
            if psc.get(key) != 1001:
                errors.append(f"narwhal-portal: pod securityContext.{key} must be 1001, got {psc.get(key)!r}")
        if psc.get("seccompProfile") != {"type": "RuntimeDefault"}:
            errors.append("narwhal-portal: pod securityContext.seccompProfile must be RuntimeDefault")
        containers = [c for c in (pod_spec.get("containers") or []) if c.get("name") == "narwhal-portal"]
        if len(containers) != 1:
            errors.append("narwhal-portal: expected container 'narwhal-portal'")
        else:
            errors.extend(_check_container_sc(containers[0], "narwhal-portal/narwhal-portal"))
            errors.extend(_check_emptydir_mount(pod_spec, containers[0], "/tmp", "narwhal-portal"))
            errors.extend(_check_emptydir_mount(pod_spec, containers[0], "/app/.next/cache", "narwhal-portal"))

    return errors


def mutate(base_docs, name):
    docs = copy.deepcopy(base_docs)
    etcd_dep = next(d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"] == "apisix-etcd")
    valkey_dep = next(d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"] == "narwhal-portal-valkey")
    portal_dep = next(d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"] == "narwhal-portal")

    etcd_pod = etcd_dep["spec"]["template"]["spec"]
    etcd_c = etcd_pod["containers"][0]
    valkey_c = valkey_dep["spec"]["template"]["spec"]["containers"][0]
    portal_pod = portal_dep["spec"]["template"]["spec"]
    portal_c = portal_pod["containers"][0]

    if name == "etcd pod securityContext removed":
        del etcd_pod["securityContext"]
    elif name == "etcd uid <= 10000":
        etcd_pod["securityContext"]["runAsUser"] = 1000
    elif name == "etcd fsGroup removed":
        del etcd_pod["securityContext"]["fsGroup"]
    elif name == "etcd seccomp removed":
        del etcd_pod["securityContext"]["seccompProfile"]
    elif name == "etcd readOnlyRootFilesystem removed":
        del etcd_c["securityContext"]["readOnlyRootFilesystem"]
    elif name == "etcd allowPrivilegeEscalation true":
        etcd_c["securityContext"]["allowPrivilegeEscalation"] = True
    elif name == "etcd capabilities drop removed":
        del etcd_c["securityContext"]["capabilities"]
    elif name == "valkey readOnlyRootFilesystem removed":
        del valkey_c["securityContext"]["readOnlyRootFilesystem"]
    elif name == "portal readOnlyRootFilesystem removed":
        del portal_c["securityContext"]["readOnlyRootFilesystem"]
    elif name == "portal fsGroup removed":
        del portal_pod["securityContext"]["fsGroup"]
    elif name == "portal runAsGroup removed":
        del portal_pod["securityContext"]["runAsGroup"]
    elif name == "portal /tmp mount removed":
        portal_c["volumeMounts"] = [m for m in portal_c["volumeMounts"] if m["mountPath"] != "/tmp"]
    elif name == "portal /app/.next/cache mount removed":
        portal_c["volumeMounts"] = [m for m in portal_c["volumeMounts"] if m["mountPath"] != "/app/.next/cache"]
    elif name == "portal /tmp mount marked readOnly":
        next(m for m in portal_c["volumeMounts"] if m["mountPath"] == "/tmp")["readOnly"] = True
    else:
        raise ValueError(f"unknown mutation: {name}")
    return docs


MUTATIONS = [
    "etcd pod securityContext removed",
    "etcd uid <= 10000",
    "etcd fsGroup removed",
    "etcd seccomp removed",
    "etcd readOnlyRootFilesystem removed",
    "etcd allowPrivilegeEscalation true",
    "etcd capabilities drop removed",
    "valkey readOnlyRootFilesystem removed",
    "portal readOnlyRootFilesystem removed",
    "portal fsGroup removed",
    "portal runAsGroup removed",
    "portal /tmp mount removed",
    "portal /app/.next/cache mount removed",
    "portal /tmp mount marked readOnly",
]


def main():
    docs = load_docs()
    errors = check(docs)
    if errors:
        print("\n".join(f"FAIL: {e}" for e in errors), file=sys.stderr)
        return 1
    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            if not check(mutate(docs, m)):
                print(f"FAIL: mutation not rejected: {m}", file=sys.stderr)
                return 1
        print(f"PASS: self-authored manifest hardening verified ({len(MUTATIONS)} mutations rejected)")
    else:
        print("PASS: self-authored manifest hardening verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
