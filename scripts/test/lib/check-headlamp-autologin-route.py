#!/usr/bin/env python3
"""R286: the Headlamp zero-click login route (D-HEADLAMP-AUTOLOGIN) behaves as intended.

2026-10-26: Headlamp v0.42 always stopped at /c/<cluster>/login and needed a "Sign In"
click. apisix-routes.yaml now injects a script into the SPA's HTML that sends that route to
/oidc?cluster=<cluster> at most once per 60 s. This check extracts that script from the
template and runs it under node against a fake browser: hard load, SPA pushState, the loop
guard, unrelated pages and the HTML-only injection guard.

Usage: check-headlamp-autologin-route.py [--mutation-verify] [<apisix-routes.yaml>]
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "gitops/charts/narwhal-platform/templates/apisix-routes.yaml"

HARNESS = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
function run(path, opts) {
  opts = opts || {};
  const store = {};
  if (opts.preset) store.narwhal_hl_auto_oidc = String(opts.preset);
  const out = { replaced: null };
  const ctx = {
    location: { pathname: path, replace: (u) => { out.replaced = u; } },
    sessionStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
    history: { pushState() {}, replaceState() {} },
    window: { addEventListener() {} },
    Date: { now: () => 1e12 }, String, encodeURIComponent, decodeURIComponent,
    setTimeout: (f) => f(),
  };
  new Function(...Object.keys(ctx), src)(...Object.values(ctx));
  if (opts.pushTo) { ctx.location.pathname = opts.pushTo; ctx.history.pushState({}, "", opts.pushTo); }
  return out.replaced;
}
console.log(JSON.stringify({
  hard: run("/c/main/login"),
  guarded: run("/c/main/login", { preset: 1e12 - 10000 }),
  expired: run("/c/main/login", { preset: 1e12 - 70000 }),
  root: run("/"),
  push: run("/", { pushTo: "/c/main/login" }),
  other: run("/c/main/pods"),
}));
"""

WANT = {
    "hard": "/oidc?cluster=main",
    "guarded": None,
    "expired": "/oidc?cluster=main",
    "root": None,
    "push": "/oidc?cluster=main",
    "other": None,
}


def route_block(text: str) -> str:
    start = text.find("- name: headlamp-login-autoredirect")
    if start < 0:
        return ""
    end = text.find("\n    - name: ", start + 1)
    return text[start:end if end > 0 else len(text)]


def problems(text: str) -> list[str]:
    block = route_block(text)
    if not block:
        return ["route headlamp-login-autoredirect is missing"]
    out = []
    for needle, why in (
        ('- "/c/*"', "must match the SPA's /c/* HTML routes"),
        ('- "/"', "must match the root document"),
        ('string.find(ct, "text/html", 1, true)', "must inject into HTML responses only"),
        ("Accept-Encoding", "must strip Accept-Encoding so the body can be rewritten"),
    ):
        if needle not in block:
            out.append(f"{needle}: {why}")
    m = re.search(r"<script>(.*?)</script>", block, re.S)
    if not m:
        return out + ["injected <script> not found"]
    if shutil.which("node") is None:
        print("WARN: node not found; behavioural part skipped")
        return out
    with tempfile.TemporaryDirectory() as d:
        js = Path(d, "snip.js")
        js.write_text(m.group(1))
        harness = Path(d, "harness.js")
        harness.write_text(HARNESS)
        res = subprocess.run(["node", str(harness), str(js)], capture_output=True, text=True)
        if res.returncode != 0:
            return out + [f"script failed under node: {res.stderr.strip()[:200]}"]
        got = json.loads(res.stdout)
    for case, want in WANT.items():
        if got.get(case) != want:
            out.append(f"behaviour '{case}': got {got.get(case)!r}, want {want!r}")
    return out


def main(argv: list[str]) -> int:
    mutation = "--mutation-verify" in argv
    args = [a for a in argv if a != "--mutation-verify"]
    text = Path(args[0] if args else ROUTES).read_text()
    found = problems(text)
    for p in found:
        print(f"FAIL {p}")
    if found:
        return 1
    print("OK headlamp-login-autoredirect: HTML-only injection, hard load + SPA navigation redirect, 60 s loop guard")
    if mutation:
        mutants = {
            "loop guard removed": text.replace("if (Date.now() - last < 60000) return;", "", 1),
            "SPA navigation not followed": text.replace('["pushState", "replaceState"]', "[]", 1),
            "injected into every content type": text.replace('string.find(ct, "text/html", 1, true)', "true", 1),
        }
        for name, mutated in mutants.items():
            if mutated == text:
                print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
                return 1
            if not problems(mutated):
                print(f"FAIL mutation '{name}' was not caught")
                return 1
            print(f"OK mutation caught: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
