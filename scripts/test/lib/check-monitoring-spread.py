#!/usr/bin/env python3
"""R311: loki is Burstable and prometheus/tempo/loki prefer different workers (D-MON-QOS-LOKI, D-MON-SPREAD).

Parses the three Application templates (no Helm directives in them). --mutation-verify re-runs
the check against a copy with one property broken and requires it to fail.
"""
import sys

import yaml

T = "gitops/charts/narwhal-apps/templates/"
HOST = "kubernetes.io/hostname"
# Observed 2026-11-06: loki ~127Mi, loki-sc-rules ~72Mi. A limit under use trades the node OOM
# kill for a container OOM kill, so values are checked, not only presence.
LOKI_LIMIT_FLOOR, LOKI_REQ_FLOOR = 512, 128
SC_LIMIT_FLOOR, SC_REQ_FLOOR = 128, 32


def mib(q):
    q = str(q)
    for suf, mult in (("Gi", 1024), ("Mi", 1), ("Ki", 1 / 1024)):
        if q.endswith(suf):
            return float(q[: -len(suf)]) * mult
    return float(q) / (1024 * 1024)


def vals(app):
    return app["spec"]["source"]["helm"]["valuesObject"]


def resources(res, label, req_floor, limit_floor, errors):
    res = res or {}
    if not res.get("requests", {}).get("cpu"):
        errors.append(f"{label}: no cpu request")
    if not res.get("requests", {}).get("memory"):
        errors.append(f"{label}: no memory request (BestEffort)")
    elif mib(res["requests"]["memory"]) < req_floor:
        errors.append(f"{label}: memory request below {req_floor}Mi")
    if not res.get("limits", {}).get("memory"):
        errors.append(f"{label}: no memory limit")
    elif mib(res["limits"]["memory"]) < limit_floor:
        errors.append(f"{label}: memory limit below {limit_floor}Mi")


def selectors(affinity):
    """label selectors of the preferred hostname anti-affinity terms."""
    out = []
    terms = ((affinity or {}).get("podAntiAffinity") or {}).get("preferredDuringSchedulingIgnoredDuringExecution") or []
    for t in terms:
        pat = t.get("podAffinityTerm", {})
        if pat.get("topologyKey") == HOST and t.get("weight", 0) > 0:
            exprs = {e["key"]: set(e.get("values", [])) for e in pat.get("labelSelector", {}).get("matchExpressions", [])
                     if e.get("operator") == "In"}
            out.append(exprs)
    return out


def check(apps):
    errors = []
    loki, tempo, prom = vals(apps["loki"]), vals(apps["tempo"]), vals(apps["prometheus-stack"])
    resources(loki.get("singleBinary", {}).get("resources"), "loki singleBinary", LOKI_REQ_FLOOR, LOKI_LIMIT_FLOOR, errors)
    resources(loki.get("sidecar", {}).get("resources"), "loki sidecar (loki-sc-rules)", SC_REQ_FLOOR, SC_LIMIT_FLOOR, errors)

    spec = prom.get("prometheus", {}).get("prometheusSpec", {})
    if spec.get("podAntiAffinity") not in ("", None, False):
        errors.append('prometheusSpec.podAntiAffinity must be "" (the chart would render a second podAntiAffinity key beside affinity)')
    affinities = {
        "prometheus": spec.get("affinity"),
        "tempo": tempo.get("affinity"),
        "loki": loki.get("singleBinary", {}).get("affinity"),
    }
    # (name, component the term must carry); loki needs component so the loki-canary DaemonSet
    # (also name=loki, on every node) does not make the term repel everywhere.
    wanted = {"prometheus": ("prometheus", None), "tempo": ("tempo", None), "loki": ("loki", "single-binary")}
    for owner, aff in affinities.items():
        # the loki chart keeps its own required self-anti-affinity (replicas 1, harmless); only
        # the terms this change adds must be preferred.
        if owner != "loki" and "requiredDuringSchedulingIgnoredDuringExecution" in ((aff or {}).get("podAntiAffinity") or {}):
            errors.append(f"{owner}: anti-affinity must be preferred, not required")
        sels = selectors(aff)
        if not sels:
            errors.append(f"{owner}: no preferred hostname podAntiAffinity")
            continue
        for other, (name, comp) in wanted.items():
            if other == owner:
                continue
            ok = any(s.get("app.kubernetes.io/name") == {name}
                     and (comp is None or s.get("app.kubernetes.io/component") == {comp}) for s in sels)
            if not ok:
                errors.append(f"{owner}: anti-affinity does not repel {other}")
        for s in sels:
            if s.get("app.kubernetes.io/name") == {"loki"} and "app.kubernetes.io/component" not in s:
                errors.append(f"{owner}: name=loki term without component=single-binary also matches loki-canary")
    return errors


def load():
    return {n: yaml.safe_load(open(T + n + ".yaml")) for n in ("loki", "tempo", "prometheus-stack")}


def mutate(name):
    a = load()
    p = vals(a["prometheus-stack"])["prometheus"]["prometheusSpec"]
    terms = "preferredDuringSchedulingIgnoredDuringExecution"
    if name == "loki without memory limit":
        del vals(a["loki"])["singleBinary"]["resources"]["limits"]
    elif name == "loki sidecar BestEffort":
        vals(a["loki"])["sidecar"]["resources"] = {}
    elif name == "tempo without anti-affinity":
        del vals(a["tempo"])["affinity"]
    elif name == "prometheus term dropped":
        del p["affinity"]["podAntiAffinity"][terms][2]
    elif name == "required instead of preferred":
        pa = vals(a["tempo"])["affinity"]["podAntiAffinity"]
        pa["requiredDuringSchedulingIgnoredDuringExecution"] = [t["podAffinityTerm"] for t in pa[terms]]
    elif name == "loki term matches canary":
        for t in vals(a["tempo"])["affinity"]["podAntiAffinity"][terms]:
            ls = t["podAffinityTerm"]["labelSelector"]
            ls["matchExpressions"] = [e for e in ls["matchExpressions"] if e["key"] != "app.kubernetes.io/component"]
    elif name == "chart soft anti-affinity left on":
        p["podAntiAffinity"] = "soft"
    return a


MUTATIONS = ["loki without memory limit", "loki sidecar BestEffort", "tempo without anti-affinity",
             "prometheus term dropped", "required instead of preferred", "loki term matches canary",
             "chart soft anti-affinity left on"]


def main():
    errors = check(load())
    if errors:
        print("\n".join(errors))
        return 1
    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            if not check(mutate(m)):
                print(f"FAIL: mutation not rejected: {m}")
                return 1
        print(f"PASS: {len(MUTATIONS)} mutations rejected")
    print("monitoring spread/qos ok")
    return 0


sys.exit(main())
