#!/usr/bin/env python3
"""R320 (D-VELERO-UI-LOGIN-563, #563): velero-ui login is restricted to the cluster-admin group.

11-3-keycloak-clients.sh builds a client-bound browser flow (velero-ui-browser): the cookie/forms
alternatives sit in a REQUIRED sub-flow and a CONDITIONAL gate (negated "user role" condition on the
client role velero-ui/login + Deny access) follows it as a REQUIRED sibling, so an SSO cookie cannot skip
the gate. The role is mapped to the cluster-admin group, the flow is bound to velero-ui only, and the
script is rerun safe. The script is parsed as text. --mutation-verify re-runs the check on broken copies.
"""
import re
import sys
from pathlib import Path

SCRIPT = Path("scripts/cluster/11-3-keycloak-clients.sh")
DOC = Path("docs/common/app-sso-permission-matrix.md")
SSO = Path("scripts/test/test-sso.sh")


def block(text):
    """The D-VELERO-UI-LOGIN-563 section, up to the JWT-key decision that follows it."""
    if "D-VELERO-UI-LOGIN-563" not in text:
        return None
    start = text.index("# D-VELERO-UI-LOGIN-563")
    end = text.find("# D-VELERO-UI-JWT-KEY", start)
    return text[start:end if end != -1 else len(text)]


def check_sso(sso):
    """Live suite: non-admins must be denied on the Keycloak Deny access page; a session for them is a FAIL."""
    errors = []
    if "KC_LOGIN_DENIED" not in sso or "restricted to the cluster-admin group" not in sso:
        errors.append("test-sso.sh does not positively detect the Keycloak Deny access page")
    if '[ -z "${loc}" ] && grep -q' not in sso:
        errors.append("test-sso.sh denial detection must require no redirect AND the deny message")
    if not re.search(r'elif \[ -n "\$\{VU_TOKEN\}" \]; then\n\s+fail "velero-ui: \$\{vu_user\} obtained a velero-ui session', sso):
        errors.append("test-sso.sh must FAIL a non-admin that obtains a velero-ui session")
    if not re.search(r'login denied by Keycloak[^\n]*\n\s+continue', sso):
        errors.append("test-sso.sh must PASS a denied non-admin and skip the in-app checks for it")
    if not re.search(r'fail "velero-ui: .*login failed but not with the Keycloak access-denied page[^\n]*\n\s+continue', sso):
        errors.append("test-sso.sh must FAIL (not pass) an undetected login failure")
    return errors


def check(text, doc, sso=""):
    b = block(text)
    if b is None:
        return ["11-3 lacks the D-VELERO-UI-LOGIN-563 section"]
    errors = []
    must = {
        "client role is created on velero-ui": r'clients/\$\{VELERO_UI_CLIENT_ID\}/roles" -r "\$\{REALM\}" -s "name=\$\{VU_ROLE\}"',
        "role is mapped to the cluster-admin group only": r'add-roles -r "\$\{REALM\}" --gname cluster-admin --cclientid velero-ui --rolename "\$\{VU_ROLE\}"',
        "the condition is the negated user-role check on velero-ui.login": r'config\.condUserRole=velero-ui\.\$\{VU_ROLE\}" -s config\.negate=true',
        "Deny access execution is added": r'execution deny-access-authenticator REQUIRED',
        "gate sub-flow is CONDITIONAL": r'flow "\$\{VU_FLOW\}-gate" CONDITIONAL',
        "auth sub-flow is REQUIRED (the gate is its sibling)": r'flow "\$\{VU_FLOW\}-auth" REQUIRED',
        "cookie is an ALTERNATIVE inside the auth sub-flow": r'"\$\{VU_FLOW\}-auth" execution auth-cookie ALTERNATIVE',
        "password form is inside the forms sub-flow": r'"\$\{VU_FLOW\}-forms" execution auth-username-password-form REQUIRED',
        "OTP rule kept": r'execution auth-otp-form REQUIRED',
        "the flow is bound to the velero-ui client via authenticationFlowBindingOverrides": r'clients/\$\{VELERO_UI_CLIENT_ID\}" -r "\$\{REALM\}" \\\n\s+-s "authenticationFlowBindingOverrides=\{\\"browser\\":\\"\$\{VU_FLOW_ID\}\\"\}"',
    }
    for what, rx in must.items():
        if not re.search(rx, b):
            errors.append(f"missing: {what}")
    # gate must come after the auth sub-flow at the top level (order decides evaluation)
    if "-auth\" REQUIRED" in b and "-gate\" CONDITIONAL" in b and b.index("-gate\" CONDITIONAL") < b.index("-auth\" REQUIRED"):
        errors.append("gate sub-flow is created before the auth sub-flow")
    # the gate must not sit in the cookie alternatives: top-level parent only
    if not re.search(r'vu_add_execution "\$\{VU_FLOW\}" flow "\$\{VU_FLOW\}-gate" CONDITIONAL', b):
        errors.append("gate sub-flow must be a direct child of the top-level flow")
    # rerun safety: lookup before create, role check before create, half-built flow is rebuilt
    if 'select(.alias==\\"${VU_FLOW}\\")' not in b or '[ -z "${VU_FLOW_ID}" ]' not in b:
        errors.append("flow creation is not guarded by an alias lookup (rerun safety)")
    if 'select(.name==\\"${VU_ROLE}\\")' not in b or '[ -z "${VU_EXISTING_ROLE}" ]' not in b:
        errors.append("role creation is not guarded by a lookup (rerun safety)")
    if "vu_flow_complete" not in b or "authenticationFlowBindingOverrides={}" not in b:
        errors.append("half-built flow is not unbound and rebuilt")
    # must not touch the realm browser flow or other clients
    if re.search(r"realms/\$\{REALM\}\"?[^\n]*browserFlow|browserFlow=", b):
        errors.append("the realm browser flow binding must not change")
    if re.search(r"authentication/flows/browser", b):
        errors.append("the built-in browser flow must not be modified")
    # no other client may be bound in this section
    if len(re.findall(r"authenticationFlowBindingOverrides=\{\\\"", b)) != 1 or "directAccessGrants" in b:
        errors.append("exactly one binding, to velero-ui, and no direct-grant change allowed")
    # secrets must not be echoed
    if re.search(r"echo[^\n]*(SECRET|PASS)", b):
        errors.append("section prints a secret")
    row = next((l for l in doc.splitlines() if l.startswith("| Velero UI |")), "")
    if "D-VELERO-UI-LOGIN-563" not in row or "velero-ui-browser" not in row:
        errors.append("permission matrix Velero UI row does not record D-VELERO-UI-LOGIN-563 and the velero-ui-browser flow")
    errors += check_sso(sso)
    return sorted(set(errors))


def mutate(name, text):
    b = block(text)
    if name == "no negate":
        return text.replace(b, b.replace(" -s config.negate=true", ""))
    if name == "deny removed":
        return text.replace(b, b.replace("execution deny-access-authenticator REQUIRED", "execution auth-cookie REQUIRED"))
    if name == "gate becomes a cookie alternative":
        return text.replace(b, b.replace('vu_add_execution "${VU_FLOW}" flow "${VU_FLOW}-gate"', 'vu_add_execution "${VU_FLOW}-auth" flow "${VU_FLOW}-gate"'))
    if name == "role not mapped to group":
        return text.replace(b, b.replace("--gname cluster-admin", "--gname developer"))
    if name == "wrong role in condition":
        return text.replace(b, b.replace("config.condUserRole=velero-ui.${VU_ROLE}", "config.condUserRole=velero-ui.other"))
    if name == "flow not bound":
        return text.replace(b, b.replace("authenticationFlowBindingOverrides={\\\"browser\\\":", "x={\\\"browser\\\":"))
    if name == "realm browser flow rebound":
        return text.replace(b, b + '\nkc_exec update "realms/${REALM}" -s browserFlow="${VU_FLOW}"\n')
    if name == "flow created unconditionally":
        return text.replace(b, b.replace('if [ -z "${VU_FLOW_ID}" ]; then', "if true; then"))
    if name == "role created unconditionally":
        return text.replace(b, b.replace('if [ -z "${VU_EXISTING_ROLE}" ]; then', "if true; then"))
    if name == "half-built flow not rebuilt":
        return text.replace(b, b.replace("authenticationFlowBindingOverrides={}", "authenticationFlowBindingOverrides=keep"))
    if name == "gate before auth":
        g = '  # CONDITIONAL gate'
        return text.replace(b, b.replace('  # REQUIRED auth sub-flow', '  vu_add_execution "${VU_FLOW}" flow "${VU_FLOW}-gate" CONDITIONAL >/dev/null\n  # REQUIRED auth sub-flow'))
    if name == "OTP rule dropped":
        return text.replace(b, b.replace("execution auth-otp-form REQUIRED", "execution auth-cookie DISABLED"))
    raise KeyError(name)


def mutate_sso(name, sso):
    if name == "sso: denial not detected":
        return sso.replace("KC_LOGIN_DENIED", "KC_X")
    if name == "sso: any failure counts as denied":
        return sso.replace('[ -z "${loc}" ] && grep -q', 'true || grep -q')
    if name == "sso: session for non-admin passes":
        return sso.replace('obtained a velero-ui session', 'obtained a velero-ui session (ok)').replace('elif [ -n "${VU_TOKEN}" ]; then\n        fail', 'elif [ -n "${VU_TOKEN}" ]; then\n        pass')
    if name == "sso: undetected failure passes":
        return sso.replace('fail "velero-ui: ${vu_user} login failed but not', 'pass "velero-ui: ${vu_user} login failed but not')
    raise KeyError(name)


MUTATIONS = ["no negate", "deny removed", "gate becomes a cookie alternative", "role not mapped to group",
             "wrong role in condition", "flow not bound", "realm browser flow rebound", "flow created unconditionally",
             "role created unconditionally", "half-built flow not rebuilt", "gate before auth", "OTP rule dropped",
             "sso: denial not detected", "sso: any failure counts as denied", "sso: session for non-admin passes",
             "sso: undetected failure passes"]


def main():
    text, doc, sso = SCRIPT.read_text(), DOC.read_text(), SSO.read_text()
    errors = check(text, doc, sso)
    if errors:
        print("\n".join(errors))
        return 1
    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            if m.startswith("sso:"):
                bad = check(text, doc, mutate_sso(m, sso))
            else:
                bad = check(mutate(m, text), doc, sso)
            if not bad:
                print(f"FAIL: mutation not rejected: {m}")
                return 1
        if not check(text, doc.replace("D-VELERO-UI-LOGIN-563", ""), sso):
            print("FAIL: mutation not rejected: matrix without decision id")
            return 1
        print(f"PASS: {len(MUTATIONS) + 1} mutations rejected")
    print("velero-ui login gate ok")
    return 0


sys.exit(main())
