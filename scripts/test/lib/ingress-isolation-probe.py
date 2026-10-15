#!/usr/bin/env python3
"""Probe cases for check-permissive-ingress-isolation.py (Narwhal#126).

Usage: ingress-isolation-probe.py good|bad|failclosed|tree. Generates each case in a temp dir (so no
fixture file can be linted or scanned by accident), runs the checker and exits non-zero listing every
case that misbehaves. `good` must pass; `bad`/`failclosed` must fail WITH the intended reason (not an
incidental parse error); `tree` drives default mode over a tiny INGRESS_ISO_ROOT.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

CHECKER = pathlib.Path(__file__).resolve().with_name("check-permissive-ingress-isolation.py")
PA = "apiVersion: security.istio.io/v1\nkind: PeerAuthentication\nmetadata:\n  name: p\n  namespace: {ns}\nspec:\n{spec}"
NOISO = "without a compensating default-deny ingress policy"


def pa(ns: str = "t", spec: str = "  mtls:\n    mode: PERMISSIVE\n") -> str:
    return PA.format(ns=ns, spec=spec)


def np(spec: str, meta: str = "namespace: t", kind: str = "NetworkPolicy", api: str = "networking.k8s.io/v1") -> str:
    body = "".join(f"  {l}\n" for l in spec.strip("\n").splitlines())
    return f"---\napiVersion: {api}\nkind: {kind}\nmetadata:\n  name: n\n  {meta}\nspec:\n{body}"


def deny(pod: str = "{}", types: str = "[Ingress]", ingress: str = "") -> str:
    return np(f"podSelector: {pod}\npolicyTypes: {types}\n{ingress}")


FROM_POD = "ingress:\n- from:\n  - podSelector: {}"
CIL = dict(kind="CiliumNetworkPolicy", api="cilium.io/v2")
# name -> manifest text
GOOD = {
    "default-deny": pa() + deny(),
    "ingress-empty-list": pa() + deny(ingress="ingress: []"),
    "from-same-ns-pods": pa() + deny(ingress=FROM_POD),
    "workload-scoped-no-policy": pa("w", "  selector:\n    matchLabels: {app: x}\n  mtls:\n    mode: PERMISSIVE\n"),
    "strict-no-policy": pa(spec="  mtls:\n    mode: STRICT\n"),
    "mesh-root-exempt": pa("istio-system").replace("name: p", "name: default"),
    "cilium-restricted": pa() + np("endpointSelector: {}\ningress:\n- fromEndpoints:\n  - matchLabels: {a: b}", **CIL),
}
BAD = {
    "missing": pa(),
    "disable-mode": pa(spec="  mtls:\n    mode: DISABLE\n"),
    "port-level-permissive": pa(spec="  mtls:\n    mode: STRICT\n  portLevelMtls:\n    80:\n      mode: PERMISSIVE\n"),
    "other-namespace": pa() + np("podSelector: {}\npolicyTypes: [Ingress]", "namespace: other"),
    "missing-namespace": pa() + np("podSelector: {}\npolicyTypes: [Ingress]", "labels: {a: b}"),
    "egress-only": pa() + deny(types="[Egress]", ingress="egress:\n- {}"),
    "no-policytypes": pa() + np("podSelector: {}\ningress: []"),
    "matchlabels": pa() + deny(pod="{matchLabels: {app: x}}"),
    "empty-matchlabels": pa() + deny(pod="{matchLabels: {}}"),
    "podselector-array": pa() + deny(pod="[]"),
    "policytypes-string": pa() + deny(types="Ingress"),
    "policytypes-lowercase": pa() + deny(types="[ingress]"),
    "ingress-not-list": pa() + deny(ingress="ingress: {}"),
    "allow-all-empty-rule": pa() + deny(ingress="ingress: [{}]"),
    "allow-all-empty-from": pa() + deny(ingress="ingress:\n- from: []"),
    "allow-all-ports-only": pa() + deny(ingress="ingress:\n- ports:\n  - port: 80"),
    "allow-all-ns-and-pod": pa() + deny(ingress="ingress:\n- from:\n  - namespaceSelector: {}\n    podSelector: {}"),
    "allow-all-ns": pa() + deny(ingress="ingress:\n- from:\n  - namespaceSelector: {}"),
    "allow-all-cidr": pa() + deny(ingress="ingress:\n- from:\n  - ipBlock: {cidr: 0.0.0.0/0}"),
    "allow-all-from-array-selector": pa() + deny(ingress="ingress:\n- from:\n  - podSelector: []"),
    "one-good-one-allow-all": pa() + deny(ingress=FROM_POD + "\n- {}"),
    "cilium-no-ingress": pa() + np("endpointSelector: {}", **CIL),
    "cilium-empty-ingress-list": pa() + np("endpointSelector: {}\ningress: []", **CIL),
    "cilium-default-deny-off": pa() + np("endpointSelector: {}\nenableDefaultDeny: {ingress: false}\ningress:\n- fromEndpoints:\n  - matchLabels: {a: b}", **CIL),
    "cilium-world": pa() + np("endpointSelector: {}\ningress:\n- fromEntities: [world]", **CIL),
    "cilium-other-namespace": pa() + np("endpointSelector: {}\ningress:\n- fromEndpoints:\n  - matchLabels: {a: b}",
                                        "namespace: other", **CIL),
    "cilium-selector": pa() + np("endpointSelector: {matchLabels: {a: b}}\ningress:\n- fromEndpoints:\n  - matchLabels: {a: b}", **CIL),
    "networkpolicy-wrong-api": pa() + np("podSelector: {}\npolicyTypes: [Ingress]", api="example.com/v1"),
}
FAILCLOSED = {  # name -> (text, suffix, expected stderr substring)
    "malformed": (pa(spec="  mtls: [\n"), ".yaml", "unparsable source"),
    "duplicate-key": (pa(spec="  mtls:\n    mode: STRICT\n    mode: PERMISSIVE\n"), ".yaml", "duplicate key"),
    "templated-pa": (pa("{{ .Values.ns }}"), ".yaml", "templated"),
    "tpl-pa": (pa() + deny(), ".tpl", "templated"),
    "non-mapping-doc": ("- kind: PeerAuthentication\n", ".yaml", "non-mapping document"),
    "selector-array": (pa(spec="  selector: []\n  mtls:\n    mode: PERMISSIVE\n"), ".yaml", "selector is not a mapping"),
    "mtls-array": (pa(spec="  mtls: []\n"), ".yaml", "mtls is not a mapping"),
    "spec-array": (pa(spec="  - mode: PERMISSIVE\n"), ".yaml", "spec is not a mapping"),
    "typo-mode": (pa(spec="  mtls:\n    mode: PERMISIVE\n"), ".yaml", "invalid mtls mode"),
    "wrong-pa-api": (pa().replace("security.istio.io", "example.com"), ".yaml", "apiVersion"),
    "list-kind": ("apiVersion: v1\nkind: List\nitems:\n- " + pa().replace("\n", "\n  ").rstrip(" "), ".yaml", NOISO),
    "list-items-not-array": ("kind: List\nitems: x\ny:\n  kind: PeerAuthentication\n", ".yaml", "List without an items array"),
    "json-dup-key": ('{"kind": "PeerAuthentication", "kind": "x"}', ".json", "duplicate key in JSON"),
}


def run(text: str, suffix: str = ".yaml") -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / f"case{suffix}"
        path.write_text(text)
        res = subprocess.run([sys.executable, "-I", str(CHECKER), str(path)], capture_output=True, text=True)
    return res.returncode, res.stderr


def tree(files: dict[str, str]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp).resolve()
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        res = subprocess.run([sys.executable, "-I", str(CHECKER)], capture_output=True, text=True,
                             env={**os.environ, "INGRESS_ISO_ROOT": str(root)})
    return res.returncode, res.stderr


def main(group: str) -> int:
    failures: list[str] = []
    if group == "good":
        for n, t in GOOD.items():
            rc, err = run(t)
            if rc != 0:
                failures.append(f"{n}: rc={rc} stderr={err!r}")
    elif group == "bad":
        for n, t in BAD.items():
            rc, err = run(t)
            if rc == 0 or NOISO not in err:
                failures.append(f"{n}: rc={rc} stderr={err!r}")
    elif group == "failclosed":
        for n, (t, suffix, want) in FAILCLOSED.items():
            rc, err = run(t, suffix)
            if rc == 0 or want not in err:
                failures.append(f"{n}: rc={rc} want {want!r} stderr={err!r}")
    elif group == "tree":
        mon = pa("monitoring").replace("name: p", "name: monitoring-permissive")
        wide = mon + "---\n" + pa("platform-system").replace("name: p", "name: platform-system-permissive")
        iso = deny().replace("namespace: t", "namespace: monitoring")
        drift = "KNOWN_ISSUES drift"
        cases = {  # name -> (files, expected rc, stderr substring)
            "control": ({"gitops/a.yaml": wide}, 0, ""),
            "stale-entry": ({"gitops/a.yaml": mon}, 1, drift),
            "new-violation": ({"gitops/a.yaml": wide + "---\n" + pa("payments")}, 1, NOISO),
            "vendored-excluded": ({"gitops/a.yaml": wide, "gitops/charts/kubernetes-dashboard/charts/k/np.yaml": iso}, 0, ""),
            "dashboard-own-templates-count": ({"gitops/a.yaml": wide, "gitops/charts/kubernetes-dashboard/templates/np.yaml": iso}, 1, drift),
            "platform-chart-counts": ({"gitops/a.yaml": wide, "gitops/charts/narwhal-platform/templates/np.yaml": iso}, 1, drift),
            "resources-count": ({"gitops/a.yaml": wide, "gitops/resources/np.yaml": iso}, 1, drift),
            "script-heredoc-counts": ({"gitops/a.yaml": wide, "scripts/cluster/x.sh": f"cat <<EOF | kubectl apply -f -\n{iso}\nEOF\n"}, 1, drift),
            "script-outside-heredoc": ({"gitops/a.yaml": wide, "scripts/cluster/x.sh": "echo x\nkind: NetworkPolicy\n"}, 1, "outside a heredoc"),
        }
        for n, (files, want_rc, want_err) in cases.items():
            rc, err = tree(files)
            if (rc != 0) != bool(want_rc) or want_err not in err:
                failures.append(f"{n}: rc={rc} want {want_rc}/{want_err!r} stderr={err!r}")
    else:
        return 2
    print("\n".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
