#!/usr/bin/env python3
"""R321 (D-KC-ADMIN-573, #573): the Keycloak master realm runs on a permanent admin, not temp-admin.

Static contract for scripts/cluster/11-1-keycloak-admin.sh and its consumers:
  - the permanent user gets the master realm role `admin` and nothing wider (temp-admin had the same);
  - its Secret is created only when absent and the password is never echoed;
  - temp-admin is deleted only after the new login AND a master-realm admin API call succeeded, and
    never when the two usernames are equal;
  - 11-1 is in the Phase 2 critical list AND in the execution loop (the loop is what runs scripts), after
    11-keycloak.sh and before 11-2;
  - the password never reaches argv in 11-1 (stdin manifest for the Secret, KC_CLI_PASSWORD via stdin for kcadm);
  - every kcadm consumer picks the Secret through keycloak_admin_secret / the keycloak-admin check
    and no consumer reads keycloak-initial-admin directly for the login.
--mutation-verify re-runs the check on broken copies and requires each to fail.
"""
import re
import sys
from pathlib import Path

NEW = "scripts/cluster/11-1-keycloak-admin.sh"
PHASE2 = "scripts/cluster/06-phase2-start.sh"
LIB = "scripts/common/lib.sh"
LIB_CONSUMERS = ["scripts/cluster/11-2-keycloak-config.sh", "scripts/cluster/11-3-keycloak-clients.sh"]
INLINE_CONSUMERS = [
    "scripts/cluster/13-2-narwhal-portal-bindings.sh",
    "scripts/test/test-sso.sh",
    "scripts/test/verify-cluster.sh",
    "scripts/test/show-credentials.sh",
]
FILES = [NEW, PHASE2, LIB, *LIB_CONSUMERS, *INLINE_CONSUMERS]


def check(src):
    errs = []
    s = src[NEW]
    if "set -euo pipefail" not in s:
        errs.append("11-1 lacks set -euo pipefail")
    if re.search(r"^\s*set -[a-z]*x", s, re.M):
        errs.append("11-1 enables xtrace (would print the password)")
    # Permanent user: only the master realm role admin may be granted.
    roles = re.findall(r"add-roles[^\n]*", s)
    if len(roles) != 1 or "--rolename admin" not in roles[0] or "--cclientid" in roles[0] or "--gname" in roles[0]:
        errs.append("11-1 must grant exactly the master realm role admin")
    if not re.search(r'PERM_USER="platform-admin"', s):
        errs.append("permanent username is not platform-admin")
    # Secret created only when absent, from generate_password.
    m = re.search(r'if ! secret_exists "\$\{PERM_SECRET\}" iam; then\n(?:[^\n]*\n){0,3}\s+NEW_PASS=\$\(generate_password\)\n\s+kubectl create -f -', s)
    if not m:
        errs.append("keycloak-admin Secret must be created only when absent, with generate_password")
    # Never print the password value.
    for line in s.splitlines():
        if re.match(r"\s*(echo|printf)\b", line) and re.search(r"\$\{?(PERM_PASS|BOOT_PASS)\b", line):
            errs.append(f"11-1 prints a password: {line.strip()[:60]}")
    # Delete gate: verified marker before the delete; username guard before the delete.
    d = s.find('kc_exec delete "users/')
    v = s.find("verified: '${PERM_USER}' logs in and administers the master realm")
    g = s.find('"${BOOT_USER}" = "${PERM_USER}"')
    if d < 0:
        errs.append("11-1 never deletes the bootstrap user")
    else:
        if not (0 <= v < d):
            errs.append("temp-admin deleted before the new credential is verified")
        if not (0 <= g < d):
            errs.append("no same-name guard before deleting the bootstrap user")
    if "kc_can_administer()" not in s or "get realms/master" not in s:
        errs.append("verification must call a master-realm admin API")
    verify_block = s[s.find("VERIFIED=false"):s.find('if [ "${VERIFIED}" != "true" ]')] if "VERIFIED=false" in s else ""
    if "kc_login" not in verify_block or "kc_can_administer" not in verify_block:
        errs.append("verify loop must log in as the permanent user and call the admin API")
    if 'exit 1' not in s[s.find('if [ "${VERIFIED}" != "true" ]'):s.find('if [ "${VERIFIED}" != "true" ]') + 200]:
        errs.append("failed verification must abort before the delete")
    # Wiring.
    p = src[PHASE2]
    crit = p[p.find("CRITICAL_SCRIPTS=("):]
    crit = crit[:crit.find("\n)")]
    names = re.findall(r'^\s+"([^"]+\.sh)"', crit, re.M)
    if "11-1-keycloak-admin.sh" not in names:
        errs.append("11-1 is not a critical Phase 2 script")
    elif not names.index("11-keycloak.sh") < names.index("11-1-keycloak-admin.sh") < names.index("11-2-keycloak-config.sh"):
        errs.append("11-1 must run after 11-keycloak.sh and before 11-2")
    m = re.search(r'^for script in ([^;]+); do$', p, re.M)
    loop = re.findall(r'"([^"]+\.sh)"', m.group(1)) if m else []
    if "11-1-keycloak-admin.sh" not in loop:
        errs.append("11-1 is not in the Phase 2 execution loop (a fresh up.sh would never run it)")
    elif not loop.index("11-keycloak.sh") < loop.index("11-1-keycloak-admin.sh") < loop.index("11-2-keycloak-config.sh"):
        errs.append("execution loop must run 11-1 after 11-keycloak.sh and before 11-2")
    # No password on argv: kcadm gets it through stdin -> KC_CLI_PASSWORD, the Secret through a stdin manifest.
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    if re.search(r"--(new-)?password\b", code):
        errs.append("11-1 passes a password flag on the command line")
    if "--from-literal" in code:
        errs.append("11-1 builds a Secret with --from-literal (password on kubectl argv)")
    if "KC_CLI_PASSWORD" not in code or "kubectl exec -i" not in code:
        errs.append("11-1 must pass the kcadm password via stdin into KC_CLI_PASSWORD")
    if "keycloak_admin_secret()" not in src[LIB] or "keycloak-initial-admin" not in src[LIB] or "keycloak-admin" not in src[LIB]:
        errs.append("lib.sh keycloak_admin_secret missing")
    for f in LIB_CONSUMERS:
        t = src[f]
        if "$(keycloak_admin_secret)" not in t:
            errs.append(f"{f} does not use keycloak_admin_secret")
        if "source /home/vagrant/scripts/common/lib.sh" not in t:
            errs.append(f"{f} does not source lib.sh")
    for f in INLINE_CONSUMERS:
        t = src[f]
        if "get secret keycloak-admin -n iam" not in t:
            errs.append(f"{f} does not prefer iam/keycloak-admin")
    for f in LIB_CONSUMERS + INLINE_CONSUMERS:
        for i, line in enumerate(src[f].splitlines(), 1):
            if re.search(r"(get secret|get_secret iam) keycloak-initial-admin", line) and "=keycloak-initial-admin" not in line:
                errs.append(f"{f}:{i} reads keycloak-initial-admin directly")
    return errs


def load():
    return {f: Path(f).read_text(encoding="utf-8") for f in FILES}


def mutations():
    def rep(f, a, b, n=1):
        def fn(src):
            assert a in src[f], (f, a)
            src[f] = src[f].replace(a, b, n)
        return fn

    def move_delete_first(src):
        s = src[NEW]
        i = s.index('BOOT_ID=$(kc_user_id "${BOOT_USER}")')
        j = s.index("# 3. ") if "# 3. " in s else s.index("if [ \"${SESSION}\" = \"bootstrap\" ]; then")
        block = s[i:s.index('\necho ""\necho "  Admin user:')]
        s = s.replace(block, "")
        src[NEW] = s[:j] + block + "\n" + s[j:]

    return {
        "role widened to realm-management": rep(NEW, "--rolename admin >/dev/null", "--rolename admin --cclientid realm-management >/dev/null"),
        "extra add-roles call": rep(NEW, "echo \"  -> password synced", "kc_exec add-roles -r master --uusername x --rolename create-realm\n  echo \"  -> password synced"),
        "delete before verification": move_delete_first,
        "same-name guard removed": rep(NEW, '"${BOOT_USER}" = "${PERM_USER}"', '"x" = "y"'),
        "failed verification no longer aborts": rep(NEW, 'keeping ${BOOT_USER}." >&2\n    exit 1', 'keeping ${BOOT_USER}." >&2'),
        "verification skips the admin API": rep(NEW, "kc_login \"${PERM_USER}\" \"${PERM_PASS}\" && kc_can_administer; then\n      VERIFIED", "kc_login \"${PERM_USER}\" \"${PERM_PASS}\"; then\n      VERIFIED"),
        "secret created unconditionally": rep(NEW, 'if ! secret_exists "${PERM_SECRET}" iam; then\n', 'if true; then\n'),
        "password echoed": rep(NEW, 'echo "  -> secret iam/${PERM_SECRET} created"', 'echo "  -> secret iam/${PERM_SECRET} created ${PERM_PASS}"'),
        "xtrace enabled": rep(NEW, "set -euo pipefail", "set -euxo pipefail"),
        "11-1 not critical": rep(PHASE2, '  "11-1-keycloak-admin.sh"\n', ""),
        "11-1 missing from the execution loop": rep(PHASE2, '"11-keycloak.sh" "11-1-keycloak-admin.sh" "11-2-keycloak-config.sh"', '"11-keycloak.sh" "11-2-keycloak-config.sh"'),
        "11-1 after 11-2 in the execution loop": rep(PHASE2, '"11-keycloak.sh" "11-1-keycloak-admin.sh" "11-2-keycloak-config.sh"', '"11-keycloak.sh" "11-2-keycloak-config.sh" "11-1-keycloak-admin.sh"'),
        "kcadm login back on --password argv": rep(NEW, '--user "$1" >/dev/null 2>&1', '--user "$1" --password "$2" >/dev/null 2>&1'),
        "set-password back on --new-password argv": rep(NEW, 'set-password -r master --userid "${PERM_ID}" >/dev/null', 'set-password -r master --userid "${PERM_ID}" --new-password "${PERM_PASS}" >/dev/null'),
        "Secret back on --from-literal": rep(NEW, 'kubectl create -f - >/dev/null <<MANIFEST', 'kubectl create secret generic x --from-literal=password="${NEW_PASS}" >/dev/null <<MANIFEST'),
        "11-1 after 11-2": rep(PHASE2, '  "11-1-keycloak-admin.sh"\n  "11-2-keycloak-config.sh"\n', '  "11-2-keycloak-config.sh"\n  "11-1-keycloak-admin.sh"\n'),
        "lib helper removed": rep(LIB, "keycloak_admin_secret()", "keycloak_admin_secret_x()"),
        "11-2 reads the bootstrap secret": rep(LIB_CONSUMERS[0], 'kubectl get secret "${KC_ADMIN_SECRET}" -n iam', "kubectl get secret keycloak-initial-admin -n iam"),
        "11-3 drops the helper": rep(LIB_CONSUMERS[1], "$(keycloak_admin_secret)", "keycloak-initial-admin"),
        "test-sso reads the bootstrap secret": rep(INLINE_CONSUMERS[1], 'get secret "${secret}" -n iam -o jsonpath=\'{.data.username}\'', "get secret keycloak-initial-admin -n iam -o jsonpath='{.data.username}'"),
        "show-credentials drops the preference": rep(INLINE_CONSUMERS[3], "get secret keycloak-admin -n iam", "get secret keycloak-xx -n iam"),
    }


def main():
    base = load()
    errs = check(base)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1
    if "--mutation-verify" in sys.argv:
        bad = 0
        muts = mutations()
        for name, fn in muts.items():
            src = dict(base)
            fn(src)
            if not check(src):
                print(f"mutation NOT caught: {name}", file=sys.stderr)
                bad += 1
        if bad:
            return 1
        print(f"keycloak permanent admin ok; {len(muts)} mutations caught")
        return 0
    print("keycloak permanent admin ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
