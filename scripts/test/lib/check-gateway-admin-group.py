"""R302: gateway-only admin UIs (APISIX openid-connect) are restricted to Keycloak group cluster-admin.

D-GATEWAY-ADMIN-GROUP: every ApisixRoute http rule with openid-connect either is in LOGIN_ONLY
(the app enforces its own authorization) or carries the shared strip-then-check serverless pair.
D-GATEWAY-ADMIN-ALL-SOURCES (#535 follow-up): the scan covers every rendered gitops chart and raw
manifest, not only narwhal-platform, and an openid-connect that arrives through an
ApisixPluginConfig (resolved per referencing route) or an ApisixGlobalRule must satisfy the same rule.
Cost: three helm renders instead of one. Escape hatch: LOGIN_ONLY, keyed by namespace/route/rule so a
same-named route in another chart is not allowlisted.
"""

import copy
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
GITOPS = ROOT / "gitops"
OIDC = "openid-connect"
KINDS = {"ApisixRoute", "ApisixPluginConfig", "ApisixGlobalRule"}
# Login-only on purpose: the app maps Keycloak groups itself (docs/common/app-sso-permission-matrix.md).
LOGIN_ONLY = {"platform-system/gitea/gitea"}
MUST_RESTRICT = {"hubble", "prometheus", "alertmanager", "nfs-quota-agent"}


def helm(name, chart, *args):
    out = subprocess.check_output(["helm", "template", name, str(GITOPS / "charts" / chart), *args], text=True)
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def render():
    """Every gitops chart (same args as check-appproject-fit.py) plus raw manifests outside charts/."""
    docs = helm("narwhal-platform", "narwhal-platform")
    docs += helm("narwhal-apps", "narwhal-apps", "--set", "repoURL=http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git",
                 "--set", "baseDomain=local.narwhal.internal", "--set", "provider=vagrant")
    docs += helm("kubernetes-dashboard", "kubernetes-dashboard")
    for path in sorted(p for ext in ("*.yaml", "*.yml") for p in GITOPS.rglob(ext) if "charts" not in p.relative_to(GITOPS).parts):
        docs += [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    return docs


def objects(node):
    """Apisix objects at any depth, so one embedded in an Application's inline values is not missed."""
    if isinstance(node, dict):
        if node.get("kind") in KINDS and isinstance(node.get("spec"), dict):
            yield node
        for v in node.values():
            yield from objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from objects(v)


def ident(obj, *rest):
    meta = obj.get("metadata", {})
    return "/".join([meta.get("namespace", ""), meta.get("name", ""), *rest])


def rules(docs):
    for obj in objects(docs):
        if obj["kind"] == "ApisixRoute":
            for rule in obj["spec"].get("http", []):
                yield obj, rule


def fn(plugin):
    return "\n".join(plugin.get("config", {}).get("functions", []))


def by_name(plugins):
    """Exact plugin-name match; a later duplicate wins, as when the controller builds the plugin map."""
    return {p["name"]: p for p in plugins if isinstance(p, dict) and "name" in p}


def check(label, plugins):
    errs = []
    oidc = plugins[OIDC]
    if not oidc.get("enable", True) or not oidc.get("config", {}).get("set_userinfo_header"):
        errs.append(f"{label}: openid-connect must stay enabled with set_userinfo_header: true")
    pre = plugins.get("serverless-pre-function", {})
    post = plugins.get("serverless-post-function", {})
    if not (pre.get("enable", True) and 'clear_header("X-Userinfo")' in fn(pre) and pre.get("config", {}).get("phase") == "rewrite"):
        errs.append(f"{label}: no rewrite-phase serverless-pre-function stripping client X-Userinfo")
    if not (post.get("enable", True) and '"cluster-admin"' in fn(post) and "X-Userinfo" in fn(post) and "ngx.exit(403)" in fn(post)
            and post.get("config", {}).get("phase") == "rewrite"):
        errs.append(f"{label}: no rewrite-phase serverless-post-function requiring group cluster-admin (403)")
    return errs


def problems(docs):
    errs = []
    seen = set()
    configs = {}
    for obj in objects(docs):
        if obj["kind"] == "ApisixPluginConfig":
            configs[(obj["metadata"].get("namespace", ""), obj["metadata"]["name"])] = by_name(obj["spec"].get("plugins", []))
    # A plugin config or global rule applies beyond one route, so it gets no allowlist.
    for obj in objects(docs):
        if obj["kind"] in ("ApisixPluginConfig", "ApisixGlobalRule"):
            plugins = by_name(obj["spec"].get("plugins", []))
            if OIDC in plugins and plugins[OIDC].get("enable", True):
                errs += check(f"{obj['kind']} {ident(obj)}", plugins)
    for obj, rule in rules(docs):
        label = ident(obj, rule["name"])
        plugins = {}
        ref = rule.get("plugin_config_name")
        if ref:
            key = (rule.get("plugin_config_namespace") or obj["metadata"].get("namespace", ""), ref)
            if key not in configs:
                errs.append(f"{label}: plugin_config_name {key[0]}/{key[1]} not found in any scanned source")
            plugins.update(configs.get(key, {}))
        plugins.update(by_name(rule.get("plugins", [])))
        if OIDC not in plugins:
            continue
        name = rule["name"]
        seen.add(name)
        if label in LOGIN_ONLY:
            continue
        if not plugins[OIDC].get("enable", True) and name not in MUST_RESTRICT:
            continue
        errs += check(label, plugins)
    for name in sorted(MUST_RESTRICT - seen):
        errs.append(f"{name}: expected openid-connect route not found")
    if {i.split("/")[-1] for i in LOGIN_ONLY} & MUST_RESTRICT:
        errs.append("LOGIN_ONLY overlaps MUST_RESTRICT")
    return errs


def mutate(docs, name, edit):
    docs = copy.deepcopy(docs)
    for _, rule in rules(docs):
        if rule["name"] == name:
            edit(rule)
    return docs


def mutation_verify(docs):
    def drop(plugin):
        return lambda r: r.__setitem__("plugins", [p for p in r["plugins"] if p["name"] != plugin])

    def subst(old, new):
        def edit(r):
            for p in r["plugins"]:
                if "functions" in p.get("config", {}):
                    p["config"]["functions"] = [f.replace(old, new) for f in p["config"]["functions"]]
        return edit

    def no_userinfo(r):
        next(p for p in r["plugins"] if p["name"] == "openid-connect")["config"]["set_userinfo_header"] = False

    def new_route(r):
        r["name"] = "alertmanager2"

    def on_docs(name, edit):
        return lambda docs: mutate(docs, name, edit)

    def hubble_plugins(docs):
        return copy.deepcopy(next(r for _, r in rules(docs) if r["name"] == "hubble")["plugins"])

    def apisix(kind, name, **spec):
        return {"apiVersion": "apisix.apache.org/v2", "kind": kind, "metadata": {"name": name, "namespace": "other-ns"}, "spec": spec}

    def route(name, plugins, **extra):
        return apisix("ApisixRoute", name, http=[{"name": name, "match": {"hosts": [f"{name}.example"], "paths": ["/*"]}, **extra,
                                                  **({"plugins": plugins} if plugins is not None else {})}])

    def oidc_only(docs):
        return [p for p in hubble_plugins(docs) if p["name"] == OIDC]

    def add(make):
        return lambda docs: copy.deepcopy(docs) + make(docs)

    muts = {
        "drop post (hubble)": on_docs("hubble", drop("serverless-post-function")),
        "drop pre/strip (prometheus)": on_docs("prometheus", drop("serverless-pre-function")),
        "wrong group (alertmanager)": on_docs("alertmanager", subst('"cluster-admin"', '"viewer"')),
        "no strip call (nfs-quota-agent)": on_docs("nfs-quota-agent", subst('clear_header("X-Userinfo")', "")),
        "userinfo header off (hubble)": on_docs("hubble", no_userinfo),
        "renamed unlisted route (alertmanager)": on_docs("alertmanager", lambda r: (drop("serverless-post-function")(r), new_route(r))),
        "other-chart route, oidc without pair": add(lambda d: [route("rogue", oidc_only(d))]),
        "other-chart route named gitea is not allowlisted": add(lambda d: [route("gitea", oidc_only(d))]),
        "oidc via plugin config": add(lambda d: [apisix("ApisixPluginConfig", "shared", plugins=oidc_only(d)),
                                                  route("viaconfig", None, plugin_config_name="shared")]),
        "unreferenced plugin config with oidc": add(lambda d: [apisix("ApisixPluginConfig", "shared", plugins=oidc_only(d))]),
        "missing plugin config reference": add(lambda d: [route("dangling", None, plugin_config_name="nope")]),
        "oidc via global rule": add(lambda d: [apisix("ApisixGlobalRule", "all", plugins=oidc_only(d))]),
        "oidc inside an Application's inline values": add(lambda d: [{"kind": "Application", "spec": {"source": {"helm": {"valuesObject": {
            "extraObjects": [route("nested", oidc_only(d))]}}}}}]),
    }
    # Must stay clean: a pair-carrying route or plugin config elsewhere, and a disabled oidc.
    clean = {
        "other-chart route with the pair": add(lambda d: [route("ok", hubble_plugins(d))]),
        "plugin config with the pair": add(lambda d: [apisix("ApisixPluginConfig", "shared", plugins=hubble_plugins(d)),
                                                       route("viaconfig", None, plugin_config_name="shared")]),
        "disabled oidc": add(lambda d: [route("off", [{**p, "enable": False} for p in oidc_only(d)])]),
        "near-miss plugin name": add(lambda d: [route("near", [{**p, "name": "openid-connect-x"} for p in oidc_only(d)])]),
    }
    for label, make in muts.items():
        if not problems(make(docs)):
            print(f"FAIL: mutation not caught: {label}")
            return 1
    for label, make in clean.items():
        if problems(make(docs)):
            print(f"FAIL: false positive: {label}")
            return 1
    print(f"PASS: R302 mutation-verify, {len(muts)} mutations caught, {len(clean)} clean controls accepted")
    return 0


def main():
    docs = render()
    errs = problems(docs)
    for e in errs:
        print("FAIL:", e)
    if errs:
        return 1
    if "--mutation-verify" in sys.argv:
        return mutation_verify(docs)
    print(f"PASS: R302 openid-connect routes restricted to cluster-admin or login-only allowlisted ({', '.join(sorted(LOGIN_ONLY))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
