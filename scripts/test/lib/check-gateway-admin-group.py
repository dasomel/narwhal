"""R302: gateway-only admin UIs (APISIX openid-connect) are restricted to Keycloak group cluster-admin.

D-GATEWAY-ADMIN-GROUP: every ApisixRoute http rule with openid-connect either is in LOGIN_ONLY
(the app enforces its own authorization) or carries the shared strip-then-check serverless pair.
"""

import copy
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "gitops/charts/narwhal-platform"
# Login-only on purpose: the app maps Keycloak groups itself (docs/common/app-sso-permission-matrix.md).
LOGIN_ONLY = {"gitea"}
MUST_RESTRICT = {"hubble", "prometheus", "alertmanager", "nfs-quota-agent"}


def render():
    out = subprocess.check_output(["helm", "template", "narwhal-platform", str(CHART)], text=True)
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def rules(docs):
    for doc in docs:
        if doc.get("kind") == "ApisixRoute":
            for rule in doc["spec"].get("http", []):
                yield rule


def fn(plugin):
    return "\n".join(plugin.get("config", {}).get("functions", []))


def problems(docs):
    errs = []
    seen = set()
    for rule in rules(docs):
        plugins = rule.get("plugins", [])
        names = [p["name"] for p in plugins]
        if "openid-connect" not in names:
            continue
        name = rule["name"]
        seen.add(name)
        if name in LOGIN_ONLY:
            continue
        oidc = plugins[names.index("openid-connect")]
        if not oidc.get("enable", True) or not oidc["config"].get("set_userinfo_header"):
            errs.append(f"{name}: openid-connect must stay enabled with set_userinfo_header: true")
        pre = [p for p in plugins if p["name"] == "serverless-pre-function" and p.get("enable", True)]
        post = [p for p in plugins if p["name"] == "serverless-post-function" and p.get("enable", True)]
        if not any('clear_header("X-Userinfo")' in fn(p) and p["config"].get("phase") == "rewrite" for p in pre):
            errs.append(f"{name}: no rewrite-phase serverless-pre-function stripping client X-Userinfo")
        if not any('"cluster-admin"' in fn(p) and "X-Userinfo" in fn(p) and "ngx.exit(403)" in fn(p)
                   and p["config"].get("phase") == "rewrite" for p in post):
            errs.append(f"{name}: no rewrite-phase serverless-post-function requiring group cluster-admin (403)")
    for name in sorted(MUST_RESTRICT - seen):
        errs.append(f"{name}: expected openid-connect route not found")
    if LOGIN_ONLY & MUST_RESTRICT:
        errs.append("LOGIN_ONLY overlaps MUST_RESTRICT")
    return errs


def mutate(docs, name, edit):
    docs = copy.deepcopy(docs)
    for rule in rules(docs):
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

    muts = {
        "drop post (hubble)": ("hubble", drop("serverless-post-function")),
        "drop pre/strip (prometheus)": ("prometheus", drop("serverless-pre-function")),
        "wrong group (alertmanager)": ("alertmanager", subst('"cluster-admin"', '"viewer"')),
        "no strip call (nfs-quota-agent)": ("nfs-quota-agent", subst('clear_header("X-Userinfo")', "")),
        "userinfo header off (hubble)": ("hubble", no_userinfo),
        "renamed unlisted route (alertmanager)": ("alertmanager", lambda r: (drop("serverless-post-function")(r), new_route(r))),
    }
    for label, (name, edit) in muts.items():
        if not problems(mutate(docs, name, edit)):
            print(f"FAIL: mutation not caught: {label}")
            return 1
    print(f"PASS: R302 mutation-verify, {len(muts)} mutations caught")
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
