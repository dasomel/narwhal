"""R305: the gateway passes portal machine-token Gitea API calls without OIDC, and nothing wider.

D-GITEA-PORTAL-API: ApisixRoute gitea-git-bypass carries a rule gitea-portal-api that skips
openid-connect only for the three portal paths on gitea-admin/narwhal-gitops, only for GET/POST,
and only when `Authorization: token <40 hex>` is present. Anything wider (blanket /api/v1,
another repo, other methods, no header condition) would open the gateway beyond R130-R133.
Cost: one helm render. Escape hatch: delete the rule (the portal then fails visibly).
"""

import copy
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "gitops/charts/narwhal-platform"
REPO = "/api/v1/repos/gitea-admin/narwhal-gitops/"
ALLOWED_PATHS = {REPO + "git/commits/*", REPO + "contents/*", REPO + "pulls"}
TOKEN_REGEX = "^token [0-9a-f]{40}$"
GITEA_HOST = "gitea." + yaml.safe_load((CHART / "values.yaml").read_text())["baseDomain"]


def render():
    out = subprocess.check_output(["helm", "template", "narwhal-platform", str(CHART)], text=True)
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def rule(docs):
    for d in docs:
        if d.get("kind") == "ApisixRoute" and d["metadata"]["name"] == "gitea-git-bypass":
            for r in d["spec"]["http"]:
                if r["name"] == "gitea-portal-api":
                    return r
    return None


def errors(r):
    if r is None:
        return ["rule gitea-portal-api missing from ApisixRoute gitea-git-bypass"]
    errs = []
    m = r.get("match", {})
    paths = set(m.get("paths", []))
    if not paths or not paths <= ALLOWED_PATHS:
        errs.append(f"paths must be a subset of {sorted(ALLOWED_PATHS)}, got {sorted(paths)}")
    if not set(m.get("methods", [])) or not set(m["methods"]) <= {"GET", "POST"}:
        errs.append(f"methods must be a non-empty subset of GET/POST, got {m.get('methods')}")
    if m.get("hosts") != [GITEA_HOST]:
        errs.append(f"hosts must be exactly [{GITEA_HOST!r}], got {m.get('hosts')}")
    hdr = [e for e in m.get("exprs", []) if e.get("subject", {}).get("scope") == "Header"
           and e["subject"].get("name") == "Authorization" and e.get("op") == "RegexMatch"
           and e.get("value") == TOKEN_REGEX]
    if not hdr:
        errs.append(f"no Authorization RegexMatch header condition with value exactly {TOKEN_REGEX!r}")
    plugins = {p["name"]: p for p in r.get("plugins", [])}
    if "openid-connect" in plugins:
        errs.append("rule must not itself enable openid-connect (it is the OIDC bypass)")
    removed = set(plugins.get("proxy-rewrite", {}).get("config", {}).get("headers", {}).get("remove", []))
    if not {"X-WEBAUTH-USER", "X-Userinfo"} <= removed:
        errs.append("proxy-rewrite must strip X-WEBAUTH-USER and X-Userinfo")
    return errs


def mutation_verify(r):
    muts = {}
    m = copy.deepcopy(r); m["match"]["paths"] = ["/api/v1/*"]; muts["blanket /api/v1"] = m
    m = copy.deepcopy(r); m["match"]["paths"] = ["/api/v1/repos/gitea-admin/other-repo/pulls"]; muts["other repo"] = m
    m = copy.deepcopy(r); m["match"]["methods"] = ["GET", "POST", "DELETE"]; muts["DELETE method"] = m
    m = copy.deepcopy(r); m["match"].pop("methods"); muts["no methods"] = m
    m = copy.deepcopy(r); m["match"]["exprs"] = []; muts["no header condition"] = m
    m = copy.deepcopy(r); m["match"]["exprs"][0]["value"] = "^token "; muts["unanchored header"] = m
    m = copy.deepcopy(r); m["match"]["exprs"][0]["value"] = "^token .*$"; muts["loose token regex"] = m
    m = copy.deepcopy(r); m["match"]["exprs"][0]["op"] = "Equal"; muts["wrong header op"] = m
    m = copy.deepcopy(r); m["match"].pop("hosts"); muts["no hosts"] = m
    m = copy.deepcopy(r); m["match"]["hosts"] = ["*.local.narwhal.internal"]; muts["wrong hosts"] = m
    m = copy.deepcopy(r); m["plugins"] = [p for p in m["plugins"] if p["name"] != "proxy-rewrite"]; muts["no header strip"] = m
    m = copy.deepcopy(r); m["plugins"].append({"name": "openid-connect", "enable": True}); muts["oidc on bypass"] = m
    missed = [n for n, x in muts.items() if not errors(x)]
    if missed:
        print("FAIL: mutations not caught:", missed)
        return 1
    print(f"PASS: R305 mutation-verify, {len(muts)} mutations caught")
    return 0


def main():
    r = rule(render())
    errs = errors(r)
    for e in errs:
        print("FAIL:", e)
    if errs:
        return 1
    if "--mutation-verify" in sys.argv:
        return mutation_verify(r)
    print("PASS: R305 gitea-portal-api is narrow (3 paths, GET/POST, token header, one repo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
