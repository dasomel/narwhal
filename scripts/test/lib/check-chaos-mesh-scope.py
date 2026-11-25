#!/usr/bin/env python3
"""R406: Chaos Mesh stays namespace-scoped to chaos-testing and its controller runs non-root.

D-OWN-CHAOS. Verifies gitops/charts/narwhal-apps/templates/chaos-mesh.yaml (valuesObject):
1. clusterScoped is false, so chart 2.8.3 binds the target-namespace ClusterRole with a
   RoleBinding (not a ClusterRoleBinding) and drops namespaces/services from the cluster-level
   ClusterRole. A missing key means the chart default `true` (cluster-wide Secret/Pod/CRD verbs).
2. controllerManager.targetNamespace equals the Application destination namespace
   (chaos-testing); the chart default is chaos-mesh, which would point the manager at a
   namespace the RoleBinding does not cover.
3. controllerManager.enableFilterNamespace is false: in namespace-scoped mode the annotation
   filter reads Namespace objects cluster-wide, which the chart's namespace-scoped RBAC omits.
4. controllerManager.securityContext is non-root with a non-zero UID/GID and RuntimeDefault seccomp.
5. R406 is wired with --mutation-verify.
--mutation-verify proves each broken variant is rejected.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "app": ROOT / "gitops/charts/narwhal-apps/templates/chaos-mesh.yaml",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}
NAMESPACE = "chaos-testing"


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    try:
        app = yaml.safe_load(texts["app"])
        vo = app["spec"]["source"]["helm"]["valuesObject"]
        dest = app["spec"]["destination"]["namespace"]
    except (yaml.YAMLError, KeyError, TypeError) as exc:
        return [f"chaos-mesh.yaml not parseable as an Application with valuesObject: {exc}"]

    if dest != NAMESPACE:
        errors.append(f"destination.namespace must be {NAMESPACE}, got {dest!r}")
    if vo.get("clusterScoped") is not False:
        errors.append(f"clusterScoped must be boolean false, got {vo.get('clusterScoped')!r}")
    cm = vo.get("controllerManager") or {}
    if cm.get("targetNamespace") != NAMESPACE:
        errors.append(f"controllerManager.targetNamespace must be {NAMESPACE}, got {cm.get('targetNamespace')!r}")
    if cm.get("enableFilterNamespace") is not False:
        errors.append("controllerManager.enableFilterNamespace must be false in namespace-scoped mode")

    sc = cm.get("securityContext") or {}
    if sc.get("runAsNonRoot") is not True:
        errors.append("controllerManager.securityContext.runAsNonRoot must be true")
    for key in ("runAsUser", "runAsGroup"):
        v = sc.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            errors.append(f"controllerManager.securityContext.{key} must be a positive integer, got {v!r}")
    if (sc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        errors.append("controllerManager.securityContext.seccompProfile.type must be RuntimeDefault")

    if "check R406 " not in texts["reg"] or "check-chaos-mesh-scope.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R406 with --mutation-verify")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("clusterScoped back to true", "app", "clusterScoped: false", "clusterScoped: true"),
    ("clusterScoped removed (chart default true)", "app", "        clusterScoped: false\n", ""),
    ("clusterScoped quoted string", "app", "clusterScoped: false", 'clusterScoped: "false"'),
    ("targetNamespace other namespace", "app", "targetNamespace: chaos-testing", "targetNamespace: default"),
    ("targetNamespace removed (chart default chaos-mesh)", "app", "          targetNamespace: chaos-testing\n", ""),
    ("destination namespace moved", "app", "    namespace: chaos-testing\n  # The controller", "    namespace: chaos-mesh\n  # The controller"),
    ("annotation filter re-enabled", "app", "enableFilterNamespace: false", "enableFilterNamespace: true"),
    ("runAsNonRoot false", "app", "runAsNonRoot: true", "runAsNonRoot: false"),
    ("runAsNonRoot removed", "app", "            runAsNonRoot: true\n", ""),
    ("runAsUser root", "app", "runAsUser: 65532", "runAsUser: 0"),
    ("runAsGroup removed", "app", "            runAsGroup: 65532\n", ""),
    ("seccomp Unconfined", "app", "type: RuntimeDefault", "type: Unconfined"),
    ("securityContext dropped", "app", "          securityContext:\n            runAsNonRoot: true\n            runAsUser: 65532\n            runAsGroup: 65532\n            seccompProfile:\n              type: RuntimeDefault\n", ""),
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
        print(f"PASS: R406 Chaos Mesh namespace-scoped + non-root ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R406 Chaos Mesh namespace-scoped + non-root")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
