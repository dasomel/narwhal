#!/usr/bin/env python3
"""R406: Pin the Chaos Mesh controller identity, namespace and explicit RBAC."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "app": ROOT / "gitops/charts/narwhal-apps/templates/chaos-mesh.yaml",
    "rbac": ROOT / "gitops/resources/chaos-mesh-rbac.yaml",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}
NAMESPACE = "chaos-testing"


SA = "chaos-controller-manager"
RBAC_API = "rbac.authorization.k8s.io"


def rule(group: str, resources: str, verbs: str, *, name: str = "") -> dict:
    result = {"apiGroups": [group], "resources": resources.split(), "verbs": verbs.split()}
    if name:
        result["resourceNames"] = [name]
    return result


# Independent allowlist for the 2.8.3 call sites documented in chaos-mesh-rbac.yaml.
ROLES = {
    ("Role", f"{SA}-experiments"): [
        rule("chaos-mesh.org",
             "awschaos azurechaos blockchaos dnschaos gcpchaos httpchaos iochaos jvmchaos "
             "kernelchaos networkchaos physicalmachinechaos podchaos stresschaos timechaos schedules workflows",
             "get list watch create update delete"),
        rule("chaos-mesh.org", "podhttpchaos podiochaos podnetworkchaos", "get list watch create update"),
        rule("chaos-mesh.org", "statuschecks workflownodes", "get list watch create delete"),
        rule("chaos-mesh.org",
             "podhttpchaos/status podiochaos/status podnetworkchaos/status statuschecks/status "
             "workflownodes/status workflows/status", "update"),
        rule("chaos-mesh.org", "schedules/finalizers workflownodes/finalizers workflows/finalizers", "update"),
        rule("chaos-mesh.org", "schedule workflow", "create"),
        rule("chaos-mesh.org", "physicalmachines", "get list watch"),
        rule("", "persistentvolumeclaims", "get list watch"),
        rule("", "pods", "get list watch create delete patch"),
        rule("", "pods/log", "get"),
        rule("", "secrets", "get list watch"),
    ],
    ("Role", f"{SA}-control-plane"): [
        rule("coordination.k8s.io", "leases", "create"),
        rule("coordination.k8s.io", "leases", "get update", name="chaos-mesh"),
        rule("discovery.k8s.io", "endpointslices", "list watch"),
        rule("", "events", "create patch"),
    ],
    ("ClusterRole", f"{SA}-cluster"): [
        rule("", "nodes persistentvolumes", "get list watch"),
        rule("authorization.k8s.io", "subjectaccessreviews", "create"),
        rule("chaos-mesh.org", "remoteclusters", "get list watch"),
    ],
}


def check_rbac(docs: list[dict]) -> list[str]:
    expected = {("ServiceAccount", SA): {
        "apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": SA, "namespace": NAMESPACE},
    }}
    for (kind, name), rules in ROLES.items():
        binding = kind + "Binding"
        metadata = {"name": name}
        if kind == "Role":
            metadata["namespace"] = NAMESPACE
        shared = {"apiVersion": f"{RBAC_API}/v1", "metadata": metadata}
        expected[kind, name] = dict(shared, kind=kind, rules=rules)
        expected[binding, name] = dict(
            shared,
            kind=binding,
            roleRef={"apiGroup": RBAC_API, "kind": kind, "name": name},
            subjects=[{"kind": "ServiceAccount", "name": SA, "namespace": NAMESPACE}],
        )

    errors = []
    for doc in docs:
        key = doc["kind"], doc["metadata"]["name"]
        if doc != expected.pop(key, None):
            errors.append(f"Unexpected RBAC object or rules: {key[0]}/{key[1]}")
    errors.extend(f"Missing RBAC object: {kind}/{name}" for kind, name in expected)
    return errors


def check(texts: dict[str, str]) -> list[str]:
    try:
        spec = yaml.safe_load(texts["app"])["spec"]
        chart, rbac = spec["sources"]
        helm = chart["helm"]
        vo = helm["valuesObject"]
        dest = spec["destination"]["namespace"]
        errors = check_rbac([doc for doc in yaml.safe_load_all(texts["rbac"]) if doc is not None])
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        return [f"Incomplete Chaos Mesh Application or RBAC manifests: {exc}"]

    if "source" in spec or chart.get("chart") != "chaos-mesh" or chart.get("path") is not None:
        errors.append("The Application must use the Chaos Mesh Helm chart and its Git RBAC source")
    if rbac != {
        "repoURL": "{{ .Values.repoURL }}",
        "targetRevision": "HEAD",
        "path": "resources",
        "directory": {"include": FILES["rbac"].name},
    }:
        errors.append("The Application must load only chaos-mesh-rbac.yaml from its Git source")
    if set(helm) != {"valuesObject"}:
        errors.append("The chart Helm source must use valuesObject without overrides")
    if (vo.get("rbac") or {}).get("create") is not False:
        errors.append("rbac.create must be boolean false")
    if (vo.get("chaosDaemon") or {}).get("podSecurityPolicy") not in (None, False) or vo.get("extraObjects"):
        errors.append("Daemon PSP and chart extraObjects must stay disabled")
    for component in ("dashboard", "dnsServer"):
        if (vo.get(component) or {}).get("create") is not False:
            errors.append(f"{component}.create must be boolean false")
    if dest != NAMESPACE:
        errors.append(f"destination.namespace must be {NAMESPACE}, got {dest!r}")
    if vo.get("clusterScoped") is not False:
        errors.append(f"clusterScoped must be boolean false, got {vo.get('clusterScoped')!r}")
    cm = vo.get("controllerManager") or {}
    if cm.get("serviceAccount") != SA or cm.get("serviceAccountCreate") is not True:
        errors.append(f"Controller Deployment must use the explicit {SA} ServiceAccount")
    if cm.get("targetNamespace") != NAMESPACE:
        errors.append(f"controllerManager.targetNamespace must be {NAMESPACE}, got {cm.get('targetNamespace')!r}")
    if cm.get("enableFilterNamespace") is not False:
        errors.append("controllerManager.enableFilterNamespace must be false in namespace-scoped mode")

    sc = cm.get("securityContext") or {}
    if sc.get("runAsNonRoot") is not True:
        errors.append("controllerManager.securityContext.runAsNonRoot must be true")
    for key in ("runAsUser", "runAsGroup"):
        v = sc.get(key)
        if type(v) is not int or v != 65532:
            errors.append(f"controllerManager.securityContext.{key} must be 65532, got {v!r}")
    if (sc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        errors.append("controllerManager.securityContext.seccompProfile.type must be RuntimeDefault")

    if "check R406 " not in texts["reg"] or "check-chaos-mesh-scope.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R406 with --mutation-verify")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("clusterScoped back to true", "app", "clusterScoped: false", "clusterScoped: true"),
    ("clusterScoped removed (chart default true)", "app", "          clusterScoped: false\n", ""),
    ("clusterScoped quoted string", "app", "clusterScoped: false", 'clusterScoped: "false"'),
    ("targetNamespace other namespace", "app", "targetNamespace: chaos-testing", "targetNamespace: default"),
    ("targetNamespace removed (chart default chaos-mesh)", "app", "            targetNamespace: chaos-testing\n", ""),
    ("destination namespace moved", "app", "    namespace: chaos-testing\n  # The controller", "    namespace: chaos-mesh\n  # The controller"),
    ("annotation filter re-enabled", "app", "enableFilterNamespace: false", "enableFilterNamespace: true"),
    ("runAsNonRoot false", "app", "runAsNonRoot: true", "runAsNonRoot: false"),
    ("runAsNonRoot removed", "app", "              runAsNonRoot: true\n", ""),
    ("runAsUser root", "app", "runAsUser: 65532", "runAsUser: 0"),
    ("runAsGroup removed", "app", "              runAsGroup: 65532\n", ""),
    ("seccomp Unconfined", "app", "type: RuntimeDefault", "type: Unconfined"),
    ("securityContext dropped", "app",
     "            securityContext:\n              runAsNonRoot: true\n              runAsUser: 65532\n"
     "              runAsGroup: 65532\n              seccompProfile:\n                type: RuntimeDefault\n", ""),
    ("runAsUser changed to 1", "app", "runAsUser: 65532", "runAsUser: 1"),
    ("chart RBAC enabled", "app", "rbac:\n            create: false", "rbac:\n            create: true"),
    ("Helm parameter restores cluster scope", "app", "      helm:\n",
     "      helm:\n        parameters: [{name: clusterScoped, value: 'true'}]\n"),
    ("Git RBAC source points elsewhere", "app", "include: chaos-mesh-rbac.yaml", "include: rbac-policies.yaml"),
    ("wildcard verb", "rbac", "verbs: [get, list, watch]", 'verbs: ["*"]'),
    ("wildcard resource", "rbac", "resources: [physicalmachines]", 'resources: ["*"]'),
    ("RemoteCluster rule dropped", "rbac",
     "  - apiGroups: [chaos-mesh.org]\n    resources: [remoteclusters]\n    verbs: [get, list, watch]\n", ""),
    ("Secret write added", "rbac", "resources: [secrets]\n    verbs: [get, list, watch]",
     "resources: [secrets]\n    verbs: [get, list, watch, patch]"),
    ("PVC reads moved to cluster scope", "rbac", "resources: [nodes, persistentvolumes]",
     "resources: [nodes, persistentvolumes, persistentvolumeclaims]"),
    ("drop R406 wiring", "reg", "check R406 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R406 Chaos Mesh namespace scope, explicit RBAC and non-root identity ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R406 Chaos Mesh namespace scope, explicit RBAC and non-root identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
