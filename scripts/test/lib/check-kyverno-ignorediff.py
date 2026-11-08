#!/usr/bin/env python3
"""R312: kyverno-policies ignoreDifferences hides only Kyverno-defaulted fields (D-KYV-IGNORE-NARROW, #571).

Static model of Argo CD's diff: apply the Application's jqPathExpressions to both the git
manifest and a copy carrying the defaults the Kyverno webhook adds, then require equality
(no permanent OutOfSync), and require that a real edit still shows (no hidden drift).
"""
import copy
import re
import sys

import yaml

APP = "gitops/charts/narwhal-apps/templates/kyverno-policies.yaml"
RES = "gitops/resources/kyverno-policies.yaml"

# Paths Kyverno is allowed to fill in; anything else in the ignore list is too broad.
ALLOWED = {
    ".spec.admission",
    ".spec.emitWarning",
    ".spec.rules[].skipBackgroundRequests",
    ".spec.rules[].validate.allowExistingViolations",
    ".spec.rules[].verifyImages[].useCache",
    ".spec.rules[].verifyImages[].attestors[].entries[].signatureAlgorithm",
    ".spec.rules[].verifyImages[].attestors[].entries[].keys.signatureAlgorithm",
    ".spec.rules[].verifyImages[].attestations[].attestors[].entries[].signatureAlgorithm",
    ".spec.rules[].verifyImages[].attestations[].attestors[].entries[].keys.signatureAlgorithm",
}


def load_app(text):
    # The template has one Helm directive (repoURL); make it a plain scalar for YAML.
    return yaml.safe_load(re.sub(r"\{\{[^}]*\}\}", "x", text))


def ignore_paths(app):
    out = []
    for entry in app["spec"].get("ignoreDifferences", []):
        if entry.get("kind") == "ClusterPolicy":
            out += entry.get("jqPathExpressions", [])
    return out


def drop(node, parts):
    if not parts:
        return
    key = parts[0]
    many = key.endswith("[]")
    key = key[:-2] if many else key
    if not isinstance(node, dict) or key not in node:
        return
    if len(parts) == 1:
        del node[key]
    elif many:
        for item in node[key] if isinstance(node[key], list) else []:
            drop(item, parts[1:])
    else:
        drop(node[key], parts[1:])


def normalize(obj, paths):
    o = copy.deepcopy(obj)
    for p in paths:
        drop(o, p.lstrip(".").split("."))
    # Argo CD adds its tracking-id annotation itself.
    o["metadata"].get("annotations", {}).pop("argocd.argoproj.io/tracking-id", None)
    return o


def kyverno_defaults(pol):
    """Defaults observed live on Kyverno 1.18 (2026-11-06)."""
    p = copy.deepcopy(pol)
    s = p["spec"]
    s.setdefault("admission", True)
    s.setdefault("emitWarning", False)
    for r in s["rules"]:
        r.setdefault("skipBackgroundRequests", True)
        if "validate" in r:
            r["validate"].setdefault("allowExistingViolations", True)
        for vi in r.get("verifyImages", []):
            vi.setdefault("useCache", True)
            groups = list(vi.get("attestors", []))
            for att in vi.get("attestations", []):
                groups += att.get("attestors", [])
            for g in groups:
                for e in g.get("entries", []):
                    if "keys" in e:
                        e["keys"].setdefault("signatureAlgorithm", "sha256")
                        e.setdefault("signatureAlgorithm", "sha256")
    p["metadata"].setdefault("annotations", {})["argocd.argoproj.io/tracking-id"] = "x"
    return p


def check(app_text, res_text):
    errors = []
    paths = ignore_paths(load_app(app_text))
    if not paths:
        errors.append("no ClusterPolicy ignoreDifferences found")
    for p in paths:
        if p not in ALLOWED:
            errors.append(f"ignore path too broad (not a Kyverno-defaulted field): {p}")
    pols = [d for d in yaml.safe_load_all(res_text) if d and d.get("kind") == "ClusterPolicy"]
    if len(pols) < 10:
        errors.append(f"expected >=10 ClusterPolicies, found {len(pols)}")
    for pol in pols:
        name = pol["metadata"]["name"]
        if "validationFailureAction" not in pol["spec"]:
            errors.append(f"{name}: validationFailureAction not explicit in git")
            continue
        live = kyverno_defaults(pol)
        if normalize(pol, paths) != normalize(live, paths):
            errors.append(f"{name}: defaulted fields still produce a diff (permanent OutOfSync)")
        # Real edits must stay visible.
        edits = []
        e = copy.deepcopy(live)
        e["metadata"]["annotations"]["policies.kyverno.io/severity"] = "changed"
        edits.append(("annotation", e))
        e = copy.deepcopy(live)
        e["spec"]["validationFailureAction"] = "Enforce" if pol["spec"]["validationFailureAction"] == "Audit" else "Audit"
        edits.append(("validationFailureAction", e))
        e = copy.deepcopy(live)
        e["spec"]["background"] = not pol["spec"]["background"]
        edits.append(("background", e))
        e = copy.deepcopy(live)
        e["spec"]["rules"][0]["name"] = "changed"
        edits.append(("rule name", e))
        e = copy.deepcopy(live)
        e["spec"]["rules"][0]["match"] = {"any": [{"resources": {"kinds": ["ConfigMap"]}}]}
        edits.append(("rule match", e))
        e = copy.deepcopy(live)
        e["spec"]["rules"][0].pop("validate", None)
        e["spec"]["rules"][0].pop("mutate", None)
        e["spec"]["rules"][0]["verifyImages"] = [{"imageReferences": ["x"]}] if "verifyImages" not in e["spec"]["rules"][0] else []
        edits.append(("rule body", e))
        for what, e in edits:
            if normalize(pol, paths) == normalize(e, paths):
                errors.append(f"{name}: a real {what} change would be hidden by ignoreDifferences")
    return errors


def main():
    app_text, res_text = open(APP).read(), open(RES).read()
    errors = check(app_text, res_text)
    if errors:
        print("\n".join(errors))
        return 1
    if "--mutation-verify" in sys.argv:
        broad = ["        - .spec.rules[].match\n", "        - .metadata.annotations\n",
                 "        - .spec.validationFailureAction\n", "        - .spec.background\n",
                 "        - .spec.rules[].validate\n", "        - .spec.rules[].verifyImages\n"]
        for line in broad:
            mutated = app_text.rstrip("\n") + "\n" + line
            if not check(mutated, res_text):
                print(f"mutation not caught: {line.strip()}")
                return 1
        # Dropping a needed defaulted-field entry must be caught as permanent OutOfSync.
        for needle in (".spec.admission", ".spec.rules[].verifyImages[].useCache",
                       ".spec.rules[].skipBackgroundRequests"):
            mutated = "\n".join(l for l in app_text.splitlines() if l.strip() != "- " + needle) + "\n"
            if not check(mutated, res_text):
                print(f"mutation not caught: removing {needle}")
                return 1
        # A policy without an explicit validationFailureAction must be caught.
        mutated = res_text.replace("  validationFailureAction: Audit\n  background: false\n  rules:\n    - name: add-default-seccomp",
                                   "  background: false\n  rules:\n    - name: add-default-seccomp")
        if mutated == res_text or not check(app_text, mutated):
            print("mutation not caught: implicit validationFailureAction")
            return 1
    print("kyverno-policies ignoreDifferences ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
