#!/usr/bin/env python3
"""R287: Gitea browser login uses Gitea's own 'keycloak' OAuth2 source (D-GITEA-OAUTH, #491).

Verifies Design 1 (12-gitea.sh helm values), Design 4 (ApisixRoute 'gitea' OIDC header flags
and zero-click redirect to /user/oauth2/keycloak), and Design 5 (rule 'gitea-login-forward'
for GET /user/login with loop guard, direct escape hatch, and redirect_to validation).
Every assertion and every mutation in --mutation-verify is scoped to its target route block
or script; a mutation whose anchor does not apply fails the run.

Usage: check-gitea-oauth-login.py [--mutation-verify] [<apisix-routes.yaml> <12-gitea.sh>]
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
GITEA_SH = ROOT / "scripts/cluster/12-gitea.sh"

EXCLUDED_PREFIXES = (
    "/user/oauth2/",
    "/user/login",
    "/user/logout",
    "/apisix/",
    "/api/",
    "/assets/",
    "/avatar",
    "/repo-avatars/",
    "/.well-known/",
    "/favicon.ico",
    "/robots.txt",
    "/manifest.json",
    "/serviceworker.js",
)

LUA_HARNESS = r"""
local gitea_fn = dofile(arg[1])
local forward_fn = dofile(arg[2])

local function pct_encode(s)
  return (string.gsub(s, "([^A-Za-z0-9_%-%.~])", function(c)
    return string.format("%%%02X", string.byte(c))
  end))
end

local function pct_decode(s)
  return (string.gsub(s, "%%(%x%x)", function(h)
    return string.char(tonumber(h, 16))
  end))
end

local function run_fn(fn, spec)
  local cleared = {}
  local set_hdrs = {}
  local resp_hdrs = {}
  local redirected = nil
  ngx = {
    req = {
      clear_header = function(k) cleared[k] = true end,
      set_header = function(k, v) set_hdrs[k] = v end,
      get_method = function() return spec.method or "GET" end,
      get_headers = function() return spec.headers or {} end,
      get_uri_args = function() return spec.args or {} end,
    },
    var = {
      uri = spec.uri or "/",
      request_uri = spec.request_uri or spec.uri or "/",
      cookie_narwhal_gitea_sso = spec.sso_cookie,
      cookie_narwhal_gitea_sso_try = spec.try_cookie,
    },
    header = resp_hdrs,
    escape_uri = pct_encode,
    unescape_uri = pct_decode,
    redirect = function(u, code)
      redirected = { url = u, code = code }
      return redirected
    end,
  }
  fn({}, {})
  return {
    cleared = cleared,
    set_hdrs = set_hdrs,
    cookie = resp_hdrs["Set-Cookie"],
    redirect = redirected,
  }
end

local html_hdrs = { Accept = "text/html,application/xhtml+xml" }
local r_nav = run_fn(gitea_fn, {
  method = "GET",
  uri = "/gitea-admin/narwhal-gitops",
  request_uri = "/gitea-admin/narwhal-gitops?tab=code",
  headers = { Accept = "text/html", ["Sec-Fetch-Mode"] = "navigate", ["Sec-Fetch-Dest"] = "document" },
})
assert(r_nav.cleared["X-WEBAUTH-USER"] == true, "gitea must clear X-WEBAUTH-USER")
assert(r_nav.set_hdrs["X-WEBAUTH-USER"] == nil, "gitea must never set X-WEBAUTH-USER")
assert(r_nav.redirect and r_nav.redirect.code == 302, "first HTML navigate GET must 302")
assert(r_nav.redirect.url == "/user/oauth2/keycloak?redirect_to=%2Fgitea-admin%2Fnarwhal-gitops%3Ftab%3Dcode", "unexpected redirect url: " .. tostring(r_nav.redirect and r_nav.redirect.url))
assert(r_nav.cookie == "narwhal_gitea_sso=1; Path=/; Secure; HttpOnly; SameSite=Lax", "unexpected session cookie: " .. tostring(r_nav.cookie))

assert(run_fn(gitea_fn, { method = "POST", uri = "/", headers = html_hdrs }).redirect == nil, "POST must not redirect")
assert(run_fn(gitea_fn, { method = "GET", uri = "/", headers = { Accept = "application/json" } }).redirect == nil, "non-HTML must not redirect")
assert(run_fn(gitea_fn, { method = "GET", uri = "/", headers = { Accept = "text/html", ["Sec-Fetch-Mode"] = "cors" } }).redirect == nil, "Sec-Fetch-Mode=cors must not redirect")
assert(run_fn(gitea_fn, { method = "GET", uri = "/", headers = { Accept = "text/html", ["Sec-Fetch-Dest"] = "iframe" } }).redirect == nil, "Sec-Fetch-Dest=iframe must not redirect")
assert(run_fn(gitea_fn, { method = "GET", uri = "/", headers = { Accept = "text/html", ["HX-Request"] = "true" } }).redirect == nil, "HX-Request must not redirect")
assert(run_fn(gitea_fn, { method = "GET", uri = "/", headers = html_hdrs, sso_cookie = "1" }).redirect == nil, "existing narwhal_gitea_sso cookie must not redirect")
for _, ex in ipairs({ "/user/oauth2/keycloak", "/user/login", "/user/logout", "/apisix/callback", "/api/v1/repos", "/assets/js/index.js", "/avatar/abc", "/repo-avatars/1", "/.well-known/openid-configuration", "/favicon.ico", "/robots.txt", "/manifest.json", "/serviceworker.js" }) do
  assert(run_fn(gitea_fn, { method = "GET", uri = ex, headers = html_hdrs }).redirect == nil, "excluded URI must not redirect: " .. ex)
end

local f_ok = run_fn(forward_fn, {
  method = "GET",
  uri = "/user/login",
  headers = html_hdrs,
  args = { redirect_to = "%2Forg%2Frepo" },
})
assert(f_ok.redirect and f_ok.redirect.code == 302, "GET /user/login must 302")
assert(f_ok.redirect.url == "/user/oauth2/keycloak?redirect_to=%2Forg%2Frepo", "forward valid redirect_to failed: " .. tostring(f_ok.redirect.url))
assert(type(f_ok.cookie) == "table" and #f_ok.cookie == 2, "forward must set two cookies")

assert(run_fn(forward_fn, { method = "GET", uri = "/user/login", headers = html_hdrs, args = { direct = "1" } }).redirect == nil, "direct=1 must pass through")
assert(run_fn(forward_fn, { method = "GET", uri = "/user/login", headers = { Accept = "application/json" }, args = {} }).redirect == nil, "non-HTML /user/login must pass through")
assert(run_fn(forward_fn, { method = "GET", uri = "/user/login", headers = html_hdrs, args = {}, try_cookie = "1" }).redirect == nil, "try_cookie must stop loop")

for _, bad_rt in ipairs({ "//evil.example/p", "https://evil.example/p", "/\\evil.example", "%2F%2Fevil.example" }) do
  local f_bad = run_fn(forward_fn, { method = "GET", uri = "/user/login", headers = html_hdrs, args = { redirect_to = bad_rt } })
  assert(f_bad.redirect and f_bad.redirect.url == "/user/oauth2/keycloak?redirect_to=%2F", "unsafe redirect_to must fall back to /: " .. bad_rt)
end
print("LUA_OK")
"""


def rule_block(text: str, name: str) -> str:
    m = re.search(
        rf"^    - name: {re.escape(name)}\n(.*?)(?=^    - name: |^---|\Z)",
        text,
        re.S | re.M,
    )
    return m.group(0) if m else ""


def extract_lua(block: str) -> str:
    m = re.search(
        r"functions:\n\s+- \|\n((?: {16}.*\n)+)",
        block,
    )
    if not m:
        return ""
    return "\n".join(line[16:] for line in m.group(1).splitlines()) + "\n"


def non_comment_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def problems(routes_text: str, gitea_sh: str) -> list[str]:
    out: list[str] = []

    # Whole apisix-routes.yaml: X-WEBAUTH-USER must never be SET anywhere.
    if 'set_header("X-WEBAUTH-USER"' in routes_text or "set_header('X-WEBAUTH-USER'" in routes_text:
        out.append('apisix-routes.yaml still sets X-WEBAUTH-USER via set_header')
    if "D-GITEA-OAUTH" not in routes_text:
        out.append("apisix-routes.yaml lacks D-GITEA-OAUTH decision record comment")

    # Design 4: ApisixRoute 'gitea'
    gitea_blk = rule_block(routes_text, "gitea")
    if not gitea_blk:
        out.append("rule 'gitea' not found in apisix-routes.yaml")
    else:
        for hdr_flag in (
            "set_userinfo_header: false",
            "set_access_token_header: false",
            "set_id_token_header: false",
        ):
            if hdr_flag not in gitea_blk:
                out.append(f"gitea route missing '{hdr_flag}'")
        if "access_token_in_authorization_header" in gitea_blk:
            out.append("gitea route still sets access_token_in_authorization_header")
        for needle, why in (
            ('ngx.req.clear_header("X-WEBAUTH-USER")', "must unconditionally clear X-WEBAUTH-USER"),
            ('if ngx.req.get_method() ~= "GET" then return end', "must guard zero-click on GET only"),
            ('string.find(accept, "text/html", 1, true)', "must require Accept to contain text/html"),
            ('headers["Sec-Fetch-Mode"]', "must inspect Sec-Fetch-Mode"),
            ('sfm ~= "navigate"', "must allow only absent or 'navigate' Sec-Fetch-Mode"),
            ('headers["Sec-Fetch-Dest"]', "must inspect Sec-Fetch-Dest"),
            ('sfd ~= "document"', "must allow only absent or 'document' Sec-Fetch-Dest"),
            ('headers["HX-Request"]', "must skip HTMX partial requests (HX-Request)"),
            ("if ngx.var.cookie_narwhal_gitea_sso then return end", "must check narwhal_gitea_sso marker cookie"),
            ("if string.sub(uri, 1, #p) == p then return end", "must exclude prefix-matched URIs"),
            (
                'ngx.header["Set-Cookie"] = "narwhal_gitea_sso=1; Path=/; Secure; HttpOnly; SameSite=Lax"',
                "must set browser-session cookie narwhal_gitea_sso=1 without Max-Age/Expires",
            ),
            (
                'return ngx.redirect("/user/oauth2/keycloak?redirect_to=" .. ngx.escape_uri(ngx.var.request_uri), 302)',
                "must 302 to /user/oauth2/keycloak with escaped request_uri",
            ),
        ):
            if needle not in gitea_blk:
                out.append(f"gitea route: {why} ({needle})")
        for prefix in EXCLUDED_PREFIXES:
            if f'"{prefix}"' not in gitea_blk:
                out.append(f"gitea route missing excluded URI prefix {prefix!r}")

    # Design 5: rule 'gitea-login-forward'
    fwd_blk = rule_block(routes_text, "gitea-login-forward")
    if not fwd_blk:
        out.append("rule 'gitea-login-forward' not found in apisix-routes.yaml")
    else:
        bypass_pos = routes_text.find("    - name: gitea-git-bypass\n")
        fwd_pos = routes_text.find("    - name: gitea-login-forward\n")
        if bypass_pos < 0 or fwd_pos < bypass_pos:
            out.append("gitea-login-forward must be placed AFTER gitea-git-bypass (R133 awk range)")
        m_prio = re.search(r"^\s+priority:\s*(\d+)\s*$", fwd_blk, re.M)
        if not m_prio or int(m_prio.group(1)) <= 100:
            out.append("gitea-login-forward must have priority > 100")
        m_paths = re.search(r"paths:\n((?:\s+- [^\n]+\n)+)", fwd_blk)
        paths = re.findall(r'"([^"]+)"', m_paths.group(1)) if m_paths else []
        if paths != ["/user/login"]:
            out.append(f"gitea-login-forward paths must be ['/user/login'], got {paths}")
        m_methods = re.search(r"methods:\n((?:\s+- [^\n]+\n)+)", fwd_blk)
        methods = re.findall(r"-\s+([A-Z]+)", m_methods.group(1)) if m_methods else []
        if methods != ["GET"]:
            out.append(f"gitea-login-forward methods must be ['GET'], got {methods}")
        if "- name: gitea-http" not in fwd_blk:
            out.append("gitea-login-forward must route to upstream gitea-http")
        for needle, why in (
            ('- "X-WEBAUTH-USER"', "must strip X-WEBAUTH-USER in proxy-rewrite"),
            ('- "X-Userinfo"', "must strip X-Userinfo in proxy-rewrite"),
            ('Strict-Transport-Security: "max-age=31536000; includeSubDomains"', "must set HSTS header"),
            ('X-Content-Type-Options: "nosniff"', "must set X-Content-Type-Options header"),
            ('if args["direct"] then return end', "must honour the direct query argument escape hatch"),
            ('string.find(accept, "text/html", 1, true)', "must pass through when Accept lacks text/html"),
            ("if ngx.var.cookie_narwhal_gitea_sso_try then return end", "must check narwhal_gitea_sso_try loop-guard cookie"),
            (
                '"narwhal_gitea_sso_try=1; Max-Age=60; Path=/; Secure; HttpOnly; SameSite=Lax"',
                "must set narwhal_gitea_sso_try=1 with Max-Age=60",
            ),
            (
                '"narwhal_gitea_sso=1; Path=/; Secure; HttpOnly; SameSite=Lax"',
                "must set session marker cookie narwhal_gitea_sso=1",
            ),
            ("rt = ngx.unescape_uri(rt)", "must decode redirect_to before validating"),
            ('string.sub(rt, 1, 1) == "/"', "must require redirect_to to start with '/'"),
            ('string.sub(rt, 1, 2) ~= "//"', "must reject protocol-relative '//' redirect_to"),
            ('not string.find(rt, "\\\\", 1, true)', "must reject backslash in redirect_to"),
            (
                'return ngx.redirect("/user/oauth2/keycloak?redirect_to=" .. ngx.escape_uri(target), 302)',
                "must 302 to /user/oauth2/keycloak with escaped target",
            ),
        ):
            if needle not in fwd_blk:
                out.append(f"gitea-login-forward: {why} ({needle})")

    # Optional Lua syntax + behavioural check when lua/luac is installed locally.
    if gitea_blk and fwd_blk and not out:
        gitea_lua = extract_lua(gitea_blk)
        fwd_lua = extract_lua(fwd_blk)
        if not gitea_lua or not fwd_lua:
            out.append("could not extract Lua functions from gitea / gitea-login-forward")
        elif shutil.which("lua") is not None:
            with tempfile.TemporaryDirectory() as d:
                f1 = Path(d, "gitea.lua")
                f2 = Path(d, "forward.lua")
                fh = Path(d, "harness.lua")
                f1.write_text(gitea_lua)
                f2.write_text(fwd_lua)
                fh.write_text(LUA_HARNESS)
                res = subprocess.run(
                    ["lua", str(fh), str(f1), str(f2)],
                    capture_output=True,
                    text=True,
                )
                if res.returncode != 0 or "LUA_OK" not in res.stdout:
                    out.append(f"Lua behavioural harness failed: {(res.stderr or res.stdout).strip()[:240]}")

    # Design 1: scripts/cluster/12-gitea.sh
    sh_code = non_comment_lines(gitea_sh)
    for required_set in (
        "--set gitea.config.service.ENABLE_REVERSE_PROXY_AUTHENTICATION=false",
        "--set gitea.config.service.ENABLE_REVERSE_PROXY_AUTO_REGISTRATION=false",
        "--set gitea.config.oauth2_client.USERNAME=preferred_username",
        "--set gitea.config.oauth2_client.ACCOUNT_LINKING=auto",
        "--set gitea.config.oauth2_client.ENABLE_AUTO_REGISTRATION=true",
        "--set gitea.config.oauth2_client.UPDATE_AVATAR=true",
        '--set gitea.config.oauth2_client.OPENID_CONNECT_SCOPES="openid profile email groups"',
    ):
        if required_set not in sh_code:
            out.append(f"12-gitea.sh missing '{required_set}'")
    for forbidden in (
        "ENABLE_REVERSE_PROXY_AUTHENTICATION=true",
        "ENABLE_REVERSE_PROXY_AUTO_REGISTRATION=true",
        "REVERSE_PROXY_AUTHENTICATION_USER",
        "REVERSE_PROXY_TRUSTED_PROXIES",
        "POD_NETWORK_CIDR",
        "REQUIRE_SIGNIN_VIEW",
        "GITEA_SEED_ADMIN_USERS",
    ):
        if forbidden in sh_code:
            out.append(f"12-gitea.sh still contains forbidden setting/var '{forbidden}'")
    if "gitea-ingress-policy.yaml" not in gitea_sh or "#160" not in gitea_sh:
        out.append("12-gitea.sh missing pointer to #160 gitea-ingress-policy.yaml boundary")
    if "D-GITEA-OAUTH" not in gitea_sh:
        out.append("12-gitea.sh missing D-GITEA-OAUTH decision comment")

    return out


def main(argv: list[str]) -> int:
    mutation = "--mutation-verify" in argv
    args = [a for a in argv if a != "--mutation-verify"]
    routes_path = Path(args[0]) if len(args) >= 1 else ROUTES
    gitea_sh_path = Path(args[1]) if len(args) >= 2 else GITEA_SH
    routes_text = routes_path.read_text()
    gitea_sh = gitea_sh_path.read_text()

    found = problems(routes_text, gitea_sh)
    for p in found:
        print(f"FAIL {p}")
    if found:
        return 1
    print(
        "OK D-GITEA-OAUTH: reverse-proxy auth disabled, USERNAME=preferred_username, "
        "gateway token headers off, zero-click + GET /user/login forward with loop guard and direct escape hatch"
    )

    if mutation:
        gitea_blk = rule_block(routes_text, "gitea")
        fwd_blk = rule_block(routes_text, "gitea-login-forward")

        def in_gitea(old: str, new: str) -> tuple[str, str]:
            return (routes_text.replace(gitea_blk, gitea_blk.replace(old, new, 1), 1), gitea_sh)

        def in_fwd(old: str, new: str) -> tuple[str, str]:
            return (routes_text.replace(fwd_blk, fwd_blk.replace(old, new, 1), 1), gitea_sh)

        def in_sh(old: str, new: str) -> tuple[str, str]:
            return (routes_text, gitea_sh.replace(old, new, 1))

        mutants: dict[str, tuple[str, str]] = {
            "drop the marker-cookie guard": in_gitea(
                "                  if ngx.var.cookie_narwhal_gitea_sso then return end\n",
                "",
            ),
            "drop the /user/oauth2/ exclusion": in_gitea(
                '                    "/user/oauth2/",\n',
                "",
            ),
            "drop the GET check": in_gitea(
                '                  if ngx.req.get_method() ~= "GET" then return end\n',
                "",
            ),
            "flip set_access_token_header to true": in_gitea(
                "            set_access_token_header: false\n",
                "            set_access_token_header: true\n",
            ),
            "re-add a set_header of X-WEBAUTH-USER": in_gitea(
                '                  ngx.req.clear_header("X-WEBAUTH-USER")\n',
                '                  ngx.req.clear_header("X-WEBAUTH-USER")\n'
                '                  ngx.req.set_header("X-WEBAUTH-USER", "admin")\n',
            ),
            "drop the try-cookie guard": in_fwd(
                "                  if ngx.var.cookie_narwhal_gitea_sso_try then return end\n",
                "",
            ),
            "drop the direct escape hatch": in_fwd(
                '                  if args["direct"] then return end\n',
                "",
            ),
            "ENABLE_REVERSE_PROXY_AUTHENTICATION back to true": in_sh(
                "  --set gitea.config.service.ENABLE_REVERSE_PROXY_AUTHENTICATION=false \\\n",
                "  --set gitea.config.service.ENABLE_REVERSE_PROXY_AUTHENTICATION=true \\\n",
            ),
            "remove USERNAME=preferred_username": in_sh(
                "  --set gitea.config.oauth2_client.USERNAME=preferred_username \\\n",
                "",
            ),
            "add REQUIRE_SIGNIN_VIEW=true": in_sh(
                "  --set gitea.config.service.ENABLE_REVERSE_PROXY_AUTO_REGISTRATION=false \\\n",
                "  --set gitea.config.service.ENABLE_REVERSE_PROXY_AUTO_REGISTRATION=false \\\n"
                "  --set gitea.config.service.REQUIRE_SIGNIN_VIEW=true \\\n",
            ),
        }

        for name, (m_routes, m_sh) in mutants.items():
            if (m_routes, m_sh) == (routes_text, gitea_sh):
                print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
                return 1
            if not problems(m_routes, m_sh):
                print(f"FAIL mutation '{name}' was not caught")
                return 1
            print(f"OK mutation caught: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
