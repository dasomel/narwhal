#!/usr/bin/env python3
"""R319 (D-VELERO-UI-563, #563): gateway handling of the velero-ui endpoints without an upstream policy check.

The route has no user identity at the gateway (app-native OAuth), so an endpoint is either closed for
everyone or open. Closed: DELETE /api/settings/velero/plugins/:name (upstream stub, unused by the UI).
Open on purpose: /api/stats*, /api/form/*, GET /api/settings/velero/{server,ui} (Dashboard, forms and
Settings need them) and /api/config (login needs it). apisix-routes.yaml is a Helm template, so it is
read as text and split into rules. --mutation-verify re-runs the check on broken copies.
"""
import re
import sys
from pathlib import Path

ROUTES = Path("gitops/charts/narwhal-platform/templates/apisix-routes.yaml")
DOC = Path("docs/common/app-sso-permission-matrix.md")

# Paths the UI calls for cluster-admin pages; no velero-ui rule may close any of them.
UI_PATHS = [
    ("GET", "/api/stats"), ("GET", "/api/stats/backups/status"), ("GET", "/api/stats/backups/latest"),
    ("GET", "/api/stats/unscheduled-namespaces"),
    ("GET", "/api/form/schedules"), ("GET", "/api/form/backups"), ("GET", "/api/form/namespaces"),
    ("GET", "/api/form/storage-locations"), ("GET", "/api/form/snapshot-locations"),
    ("GET", "/api/form/secrets"), ("GET", "/api/form/config-maps"),
    ("GET", "/api/settings/velero/server"), ("GET", "/api/settings/velero/ui"),
    ("GET", "/api/settings/velero/plugins"), ("GET", "/api/config"),
    ("DELETE", "/api/backups/some-backup"), ("POST", "/api/backups"),
]
DELETE_PLUGIN = ["/api/settings/velero/plugins/foo", "/API/Settings/Velero/Plugins/foo", "//api//settings//velero//plugins//foo",
                 "/api/settings/velero/plugins/a%20b"]


def rules(text):
    """velero-ui ApisixRoute rules as {name: body}."""
    doc = text.split("  name: velero-ui\n  namespace: platform-system\nspec:\n  http:\n", 1)[1]
    doc = doc.split("\n---", 1)[0]
    parts = re.split(r"\n    - name: ", "\n" + doc)[1:]
    return {p.split("\n", 1)[0].strip(): p for p in parts}


def scalar(body, key):
    m = re.search(rf"^\s+{key}: (.*)$", body, re.M)
    return m.group(1).strip() if m else None


def blocks(rule, method, path):
    """True when this rule answers 403 for (method, path)."""
    if "return ngx.exit(403)" not in rule:
        return False
    methods = re.findall(r"^\s+- ([A-Z]+)$", rule.split("methods:", 1)[1].split("exprs:", 1)[0].split("upstreams:", 1)[0], re.M) \
        if "methods:" in rule else []
    if methods and method not in methods:
        return False
    rx = scalar(rule, "value")
    op = scalar(rule, "op")
    if rx and op:
        flags = re.I if op == "RegexMatchCaseInsensitive" else 0
        return re.match(rx.strip('"'), path.replace("%20", " "), flags) is not None
    return False


def check(text, doc):
    errors = []
    try:
        rs = rules(text)
    except IndexError:
        return ["velero-ui ApisixRoute not found"]
    plug = rs.get("velero-ui-block-plugin-delete")
    if plug is None:
        return ["velero-ui-block-plugin-delete rule missing"]
    if scalar(plug, "priority") != "10":
        errors.append("plugin-delete rule needs priority 10 (the catch-all route has none)")
    if '- "/*"' not in plug:
        errors.append("plugin-delete rule must match every path (path match is case-sensitive)")
    if "velero-ui.{{ .Values.baseDomain }}" not in plug:
        errors.append("plugin-delete rule must be scoped to the velero-ui host")
    if re.findall(r"^\s+- ([A-Z]+)$", plug.split("methods:", 1)[1].split("exprs:", 1)[0], re.M) != ["DELETE"]:
        errors.append("plugin-delete rule must match method DELETE only")
    if scalar(plug, "op") != "RegexMatchCaseInsensitive":
        errors.append("plugin-delete rule must use RegexMatchCaseInsensitive (Express routing is case-insensitive)")
    for p in DELETE_PLUGIN:
        if not blocks(plug, "DELETE", p):
            errors.append(f"plugin-delete rule does not block DELETE {p}")
    # the earlier D-VELERO-UI-ADMIN-ONLY rule stays
    bulk = rs.get("velero-ui-block-bulk-download")
    if bulk is None or not blocks(bulk, "POST", "/api/backups/download"):
        errors.append("bulk-download rule missing or no longer blocks POST /api/backups/download")
    # nothing the UI needs may be closed by any rule on this host
    for name, rule in rs.items():
        for method, path in UI_PATHS:
            if blocks(rule, method, path):
                errors.append(f"rule {name} closes {method} {path}, which the UI needs")
    # documentation names the decision
    if "D-VELERO-UI-563" not in text:
        errors.append("apisix-routes.yaml lacks the D-VELERO-UI-563 decision comment")
    if "plugin" not in doc.split("Velero UI |", 1)[1].split("\n", 1)[0].lower() or "D-VELERO-UI-563" not in doc:
        errors.append("permission matrix does not record D-VELERO-UI-563")
    return sorted(set(errors))


def mutate(name, text):
    plug = rules(text)["velero-ui-block-plugin-delete"]
    if name == "rule removed":
        return text.replace(plug, "").replace("    - name: \n", "")
    if name == "method GET":
        return text.replace(plug, plug.replace("- DELETE", "- GET"))
    if name == "case-sensitive regex":
        return text.replace(plug, plug.replace("RegexMatchCaseInsensitive", "RegexMatch"))
    if name == "regex without slash tolerance":
        return text.replace(plug, plug.replace("^/+api/+settings/+velero/+plugins/+.+$", "^/api/settings/velero/plugins/.+$"))
    if name == "priority dropped":
        return text.replace(plug, plug.replace("      priority: 10\n", ""))
    if name == "stats closed too":
        return text.replace(plug, plug.replace("plugins/+.+$", "(plugins/+.+|stats.*)$").replace("methods:\n          - DELETE", "methods:\n          - DELETE\n          - GET"))
    if name == "form closed too":
        return text.replace(plug, plug.replace('"^/+api/+settings/+velero/+plugins/+.+$"', '"^/+api/+(settings/+velero/+plugins|form)/+.+$"').replace("methods:\n          - DELETE", "methods:\n          - DELETE\n          - GET"))
    if name == "bulk-download rule dropped":
        bulk = rules(text)["velero-ui-block-bulk-download"]
        return text.replace(bulk, "")
    raise KeyError(name)


MUTATIONS = ["rule removed", "method GET", "case-sensitive regex", "regex without slash tolerance",
             "priority dropped", "stats closed too", "form closed too", "bulk-download rule dropped"]


def main():
    text, doc = ROUTES.read_text(), DOC.read_text()
    errors = check(text, doc)
    if errors:
        print("\n".join(errors))
        return 1
    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            if not check(mutate(m, text), doc):
                print(f"FAIL: mutation not rejected: {m}")
                return 1
        # doc mutation: matrix without the decision id
        if not check(text, doc.replace("D-VELERO-UI-563", "")):
            print("FAIL: mutation not rejected: matrix without decision id")
            return 1
        print(f"PASS: {len(MUTATIONS) + 1} mutations rejected")
    print("velero-ui endpoint gateway policy ok")
    return 0


sys.exit(main())
