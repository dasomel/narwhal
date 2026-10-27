#!/usr/bin/env python3
"""R290: every control-plane node gets the 7 kube-apiserver OIDC flags on both providers,
and any propagation or verification failure is fail-closed (D-APISERVER-OIDC-ALL).

2026-10-27: on Kakao, 11-4-keycloak-apiserver.sh tried node-to-node `sshpass -p vagrant`
to master-2/3 and swallowed the failure (`2>/dev/null || echo WARN`), while verify-cluster.sh
and test-sso.sh inspected only the local manifest or `.items[0]` of kube-apiserver pods.
master-2 and master-3 had zero `--oidc-*` flags, so ~2/3 of Dashboard/Headlamp/kubectl OIDC
requests reaching the in-cluster `kubernetes` Service failed with 401 ("invalid bearer token").

Usage: check-apiserver-oidc-all-masters.py [--mutation-verify]
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NODE_SH = ROOT / "scripts/cluster/11-4b-apiserver-oidc-node.sh"
APISERVER_SH = ROOT / "scripts/cluster/11-4-keycloak-apiserver.sh"
KAKAO_SH = ROOT / "scripts/cloud/apply-apiserver-oidc-kakao.sh"
PROVISION_SH = ROOT / "scripts/cloud/provision-kakao.sh"
VERIFY_SH = ROOT / "scripts/test/verify-cluster.sh"
TEST_SSO_SH = ROOT / "scripts/test/test-sso.sh"

EXPECTED_YML_FLAGS = (
    "--oidc-issuer-url=${OIDC_ISSUER_URL}",
    "--oidc-client-id=kubernetes",
    "--oidc-username-claim=preferred_username",
    "--oidc-groups-claim=groups",
    "--oidc-username-prefix=oidc:",
    "--oidc-groups-prefix=oidc:",
    "--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt",
)

EXPECTED_CHECK_TAILS = (
    "oidc-client-id=kubernetes",
    "oidc-username-claim=preferred_username",
    "oidc-groups-claim=groups",
    "oidc-username-prefix=oidc:",
    "oidc-groups-prefix=oidc:",
    "oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt",
)


def code_only(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def sh_function_block(text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\(\)\s*\{{.*?\n\}}\n", text, re.M | re.S)
    return m.group(0) if m else ""


def section_block(text: str, start_anchor: str, end_anchor: str) -> str:
    i = text.find(start_anchor)
    if i < 0:
        return ""
    j = text.find(end_anchor, i + len(start_anchor))
    return text[i:j] if j >= 0 else text[i:]


def problems(sources: dict[str, str]) -> list[str]:
    out: list[str] = []
    node_txt = sources["node"]
    api_txt = sources["apiserver"]
    kakao_txt = sources["kakao"]
    prov_txt = sources["provision"]
    verify_txt = sources["verify"]
    sso_txt = sources["sso"]

    node_code = code_only(node_txt)
    api_code = code_only(api_txt)
    kakao_code = code_only(kakao_txt)
    prov_code = code_only(prov_txt)

    # 1. Node script (11-4b-apiserver-oidc-node.sh) is the single writer of the 7 flags.
    if "D-APISERVER-OIDC-ALL" not in node_txt:
        out.append("11-4b-apiserver-oidc-node.sh lacks D-APISERVER-OIDC-ALL decision record")
    yq_m = re.search(r'yq\s+-i\s+"\.spec\.containers\[0\]\.command\s*\+=\s*\[(.*?)\]"', node_code, re.S)
    if not yq_m:
        out.append("11-4b-apiserver-oidc-node.sh missing yq .spec.containers[0].command += [...] block")
    else:
        yq_body = yq_m.group(1)
        for flag in EXPECTED_YML_FLAGS:
            if flag not in yq_body:
                out.append(f"11-4b-apiserver-oidc-node.sh yq block missing flag '{flag}'")
    for needle, desc in (
        ('[ ! -s "${CA_SRC}" ]', "must refuse missing or empty CA source file"),
        ("-----BEGIN CERTIFICATE-----", "must require PEM certificate header"),
        ('openssl x509 -in "${CA_SRC}" -noout', "must validate CA source with openssl x509 before install"),
        ("kube-apiserver.yaml.pre-oidc-", "must write .pre-oidc-<UTC> backup"),
        ('if [ "${backup_dir_real}" = "${manifest_dir}" ]', "must refuse backup inside manifests dir"),
        (".kube-apiserver.yaml.oidc.tmp", "must stage to dotfile before atomic mv"),
        ('mv "${stage_file}" "${APISERVER_MANIFEST}"', "must atomically mv staged file over manifest"),
        ("https://127.0.0.1:6443/readyz", "must default READYZ_URL to https://127.0.0.1:6443/readyz"),
        ('if [ "${ca_changed}" = "true" ]', "must restart static pod when CA changed and flags exist"),
    ):
        if needle not in node_code:
            out.append(f"11-4b-apiserver-oidc-node.sh: {desc} ({needle})")

    # Neither 11-4 nor apply-apiserver-oidc-kakao.sh may write the flags directly.
    for label, code in (("11-4-keycloak-apiserver.sh", api_code), ("apply-apiserver-oidc-kakao.sh", kakao_code)):
        if "yq -i" in code or "--oidc-client-id=kubernetes" in code:
            out.append(f"{label} writes OIDC flags directly instead of delegating to 11-4b-apiserver-oidc-node.sh")

    # 2. 11-4-keycloak-apiserver.sh calls the node script, has no swallowed failure, and handles PROVIDER=kakao.
    if "D-APISERVER-OIDC-ALL" not in api_txt:
        out.append("11-4-keycloak-apiserver.sh lacks D-APISERVER-OIDC-ALL comment")
    oidc_sec = section_block(api_code, 'echo "=== Configuring API Server for OIDC ==="', 'echo "=== [11-4-keycloak-apiserver.sh]')
    if not oidc_sec:
        out.append("11-4-keycloak-apiserver.sh OIDC section missing")
    else:
        if "11-4b-apiserver-oidc-node.sh" not in oidc_sec:
            out.append("11-4-keycloak-apiserver.sh does not invoke 11-4b-apiserver-oidc-node.sh")
        if 'if [ "${PROVIDER:-vagrant}" = "kakao" ]' not in oidc_sec:
            out.append("11-4-keycloak-apiserver.sh missing PROVIDER=kakao guard")
        if "apply-apiserver-oidc-kakao.sh" not in oidc_sec:
            out.append("11-4-keycloak-apiserver.sh PROVIDER=kakao branch does not name apply-apiserver-oidc-kakao.sh")
        if re.search(r"\|\|\s*true\b", oidc_sec) or re.search(r"\|\|\s*echo\s+[\"']?WARN", oidc_sec):
            out.append("11-4-keycloak-apiserver.sh OIDC section still swallows failure (|| true or || echo WARN)")
        if "2>/dev/null" in oidc_sec and ("sshpass" in oidc_sec.split("2>/dev/null")[-1] or "scp" in oidc_sec):
            # Ensure neither sshpass scp nor sshpass ssh suppresses stderr or exit status
            for line in oidc_sec.splitlines():
                if "sshpass" in line and ("2>/dev/null" in line or "||" in line):
                    out.append(f"11-4-keycloak-apiserver.sh swallows sshpass error: {line.strip()}")
        if 'if ! sshpass -p "vagrant" scp' not in oidc_sec or 'if ! sshpass -p "vagrant" ssh' not in oidc_sec:
            out.append("11-4-keycloak-apiserver.sh Vagrant loop must guard both sshpass scp and sshpass ssh with `if ! ...`")

    # 3. apply-apiserver-oidc-kakao.sh: lib-ssh.sh, master-1 CA read + openssl verify, serial master-2..N apply, mirror pod count check.
    if "D-APISERVER-OIDC-ALL" not in kakao_txt:
        out.append("apply-apiserver-oidc-kakao.sh lacks D-APISERVER-OIDC-ALL comment")
    for needle, desc in (
        ("source scripts/cloud/lib-ssh.sh", "must source scripts/cloud/lib-ssh.sh"),
        ('"${KAKAO_SSH_OPTS[@]}"', "must use KAKAO_SSH_OPTS"),
        ("sudo cat /etc/kubernetes/pki/oidc-ca.crt", "must read /etc/kubernetes/pki/oidc-ca.crt from master-1"),
        ("openssl x509 -in", "must verify master-1 CA cert with openssl x509"),
        ('for ip in "${MASTERS[@]:1}"', "must iterate over master-2..N"),
        ("11-4b-apiserver-oidc-node.sh", "must run 11-4b-apiserver-oidc-node.sh on each master"),
        ("component=kube-apiserver", "must inspect kube-apiserver mirror pods after applying"),
        ("len(oidc_flags) < 7", "must fail if any kube-apiserver mirror pod has fewer than 7 --oidc-* flags"),
    ):
        if needle not in kakao_code:
            out.append(f"apply-apiserver-oidc-kakao.sh: {desc} ({needle})")
    for line in kakao_code.splitlines():
        if ("ssh_node" in line or "11-4b-apiserver-oidc-node.sh" in line) and re.search(r"\|\|\s*(true|echo\b)", line):
            out.append(f"apply-apiserver-oidc-kakao.sh swallows failure: {line.strip()}")

    # 4. provision-kakao.sh: runs apply-apiserver-oidc-kakao.sh at the end of stage_phase2 and offers `oidc` stage.
    if not re.search(r"usage:.*\|phase2\|oidc\|all>", prov_code):
        out.append("provision-kakao.sh usage line must include `oidc` stage")
    p2_blk = sh_function_block(prov_code, "stage_phase2")
    if not p2_blk:
        out.append("provision-kakao.sh missing stage_phase2()")
    else:
        if "./scripts/cloud/apply-apiserver-oidc-kakao.sh" not in p2_blk and "stage_oidc" not in p2_blk:
            out.append("provision-kakao.sh stage_phase2() does not run apply-apiserver-oidc-kakao.sh after 06-phase2-start.sh")
        p2_idx = p2_blk.find("cluster/06-phase2-start.sh")
        oidc_idx = p2_blk.find("apply-apiserver-oidc-kakao.sh")
        mark_idx = p2_blk.find('mark_done "${MASTER1}" phase2')
        if p2_idx < 0 or oidc_idx < p2_idx or (mark_idx >= 0 and oidc_idx > mark_idx):
            out.append("provision-kakao.sh stage_phase2() must run apply-apiserver-oidc-kakao.sh after 06-phase2-start.sh and before mark_done")
    oidc_blk = sh_function_block(prov_code, "stage_oidc")
    if not oidc_blk or "./scripts/cloud/apply-apiserver-oidc-kakao.sh" not in oidc_blk:
        out.append("provision-kakao.sh missing stage_oidc() calling ./scripts/cloud/apply-apiserver-oidc-kakao.sh")
    if not re.search(r"^\s*oidc\)\s+run_stage\s+stage_oidc\s*;;", prov_code, re.M):
        out.append("provision-kakao.sh case statement missing `oidc) run_stage stage_oidc ;;`")

    # 5. verify-cluster.sh and test-sso.sh: no .items[0] for kube-apiserver OIDC check, iterate all pods, check all 7 flags.
    verify_sec = section_block(
        verify_txt,
        "# API server OIDC flags on every kube-apiserver pod",
        "# Kubernetes realm existence",
    )
    if not verify_sec:
        out.append("verify-cluster.sh API server OIDC section missing")
    else:
        v_code = code_only(verify_sec)
        if ".items[0]" in v_code or "/etc/kubernetes/manifests/kube-apiserver.yaml" in v_code:
            out.append("verify-cluster.sh still checks only .items[0] or local manifest")
        if "{range .items[*]}" not in v_code or "while IFS=" not in v_code:
            out.append("verify-cluster.sh does not iterate over all kube-apiserver pods")
        if "oidc-issuer-url=https://keycloak.${DOMAIN}/realms/narwhal" not in v_code:
            out.append("verify-cluster.sh missing oidc-issuer-url flag check")
        for tail in EXPECTED_CHECK_TAILS:
            if tail not in v_code:
                out.append(f"verify-cluster.sh missing check for '{tail}'")
        if 'fail "API server OIDC (${pod_name}):' not in v_code:
            out.append("verify-cluster.sh does not fail naming the pod on missing OIDC flags")

    sso_cfg_sec = section_block(
        sso_txt,
        "# K8s API Server OIDC",
        "#=========================================\n# 7. HTTPS ENDPOINTS",
    )
    sso_rbac_sec = section_block(
        sso_txt,
        "# 8. OIDC RBAC (K8s Access Control)",
        "test_rbac()",
    )
    for sec_name, sec_txt in (("test-sso.sh [6/9 config]", sso_cfg_sec), ("test-sso.sh [8/9 rbac]", sso_rbac_sec)):
        if not sec_txt:
            out.append(f"{sec_name} section missing")
            continue
        s_code = code_only(sec_txt)
        if ".items[0]" in s_code or "/etc/kubernetes/manifests/kube-apiserver.yaml" in s_code:
            out.append(f"{sec_name} still uses .items[0] or local manifest")
        if "{range .items[*]}" not in s_code or "while IFS=" not in s_code:
            out.append(f"{sec_name} does not iterate over all kube-apiserver pods")
        if "oidc-issuer-url=${KEYCLOAK_URL}/realms/${REALM}" not in s_code:
            out.append(f"{sec_name} missing oidc-issuer-url flag check")
        for tail in EXPECTED_CHECK_TAILS:
            if tail not in s_code:
                out.append(f"{sec_name} missing check for '{tail}'")
        if "(${pod_name})" not in s_code:
            out.append(f"{sec_name} does not name pod_name in pass/fail output")

    return out


def main(argv: list[str]) -> int:
    mutation = "--mutation-verify" in argv
    sources = {
        "node": NODE_SH.read_text(),
        "apiserver": APISERVER_SH.read_text(),
        "kakao": KAKAO_SH.read_text(),
        "provision": PROVISION_SH.read_text(),
        "verify": VERIFY_SH.read_text(),
        "sso": TEST_SSO_SH.read_text(),
    }

    found = problems(sources)
    for p in found:
        print(f"FAIL {p}")
    if found:
        return 1
    print(
        "OK D-APISERVER-OIDC-ALL: 11-4b-apiserver-oidc-node.sh is sole 7-flag writer, "
        "11-4 and apply-apiserver-oidc-kakao.sh fail closed, provision-kakao.sh wires phase2+oidc, "
        "verify-cluster.sh and test-sso.sh inspect all kube-apiserver pods"
    )

    if mutation:
        def mutate(key: str, old: str, new: str) -> dict[str, str]:
            d = dict(sources)
            d[key] = d[key].replace(old, new, 1)
            return d

        mutants: dict[str, dict[str, str]] = {
            "drop --oidc-groups-prefix=oidc: from node script yq block": mutate(
                "node",
                '    \\"--oidc-groups-prefix=oidc:\\",\n',
                "",
            ),
            "drop openssl x509 CA verification from node script": mutate(
                "node",
                '  || ! openssl x509 -in "${CA_SRC}" -noout -subject >/dev/null 2>&1; then\n',
                "  || false; then\n",
            ),
            "drop backup-outside-manifests guard from node script": mutate(
                "node",
                'if [ "${backup_dir_real}" = "${manifest_dir}" ]; then\n',
                'if false; then\n',
            ),
            "drop CA-changed static pod restart from node script": mutate(
                "node",
                '  if [ "${ca_changed}" = "true" ]; then\n',
                "  if false; then\n",
            ),
            "reintroduce swallowed || true on scp in 11-4": mutate(
                "apiserver",
                '    if ! sshpass -p "vagrant" scp -o StrictHostKeyChecking=no /etc/kubernetes/pki/oidc-ca.crt \\\n'
                '      "vagrant@${MASTER_IP}:/tmp/oidc-ca.crt"; then',
                '    sshpass -p "vagrant" scp -o StrictHostKeyChecking=no /etc/kubernetes/pki/oidc-ca.crt \\\n'
                '      "vagrant@${MASTER_IP}:/tmp/oidc-ca.crt" || true\n'
                '    if false; then',
            ),
            "inline --oidc-client-id=kubernetes in 11-4": mutate(
                "apiserver",
                'echo "Applying OIDC CA + flags to master-1..."\n',
                'echo "Applying OIDC CA + flags to master-1..."\n'
                '# inline drift: "--oidc-client-id=kubernetes"\n'
                'true "--oidc-client-id=kubernetes"\n',
            ),
            "drop PROVIDER=kakao branch in 11-4": mutate(
                "apiserver",
                'if [ "${PROVIDER:-vagrant}" = "kakao" ]; then\n',
                'if [ "${PROVIDER:-vagrant}" = "never" ]; then\n',
            ),
            "swallow node script failure in apply-apiserver-oidc-kakao.sh": mutate(
                "kakao",
                '  if ! ssh_node "${ip}" "sudo env PROVIDER=kakao DOMAIN=${DOMAIN} NODE_IP=${ip} bash ${STAGE_DIR}/scripts/cluster/11-4b-apiserver-oidc-node.sh ${OIDC_ISSUER_URL} /tmp/oidc-ca.crt"; then\n',
                '  ssh_node "${ip}" "sudo env PROVIDER=kakao DOMAIN=${DOMAIN} NODE_IP=${ip} bash ${STAGE_DIR}/scripts/cluster/11-4b-apiserver-oidc-node.sh ${OIDC_ISSUER_URL} /tmp/oidc-ca.crt" || echo "WARN"\n'
                '  if false; then\n',
            ),
            "drop apply-apiserver-oidc-kakao.sh from stage_phase2 in provision-kakao.sh": mutate(
                "provision",
                "    if ! ./scripts/cloud/apply-apiserver-oidc-kakao.sh; then\n",
                "    if ! true; then\n",
            ),
            "drop oidc case branch in provision-kakao.sh": mutate(
                "provision",
                "    oidc)   run_stage stage_oidc ;;\n",
                "",
            ),
            "revert verify-cluster.sh to .items[0]": mutate(
                "verify",
                '  APISERVER_POD_LINES=$(kubectl get pods -n kube-system -l component=kube-apiserver \\\n'
                '    -o jsonpath=\'{range .items[*]}{.metadata.name}{"\\t"}{.spec.containers[0].command}{"\\n"}{end}\' 2>/dev/null || true)\n',
                '  APISERVER_POD_LINES=$(kubectl get pods -n kube-system -l component=kube-apiserver \\\n'
                '    -o jsonpath=\'{.items[0].spec.containers[0].command}\' 2>/dev/null || true)\n',
            ),
            "revert test-sso.sh rbac check to .items[0]": mutate(
                "sso",
                '  APISERVER_POD_LINES=$(kubectl get pods -n kube-system -l component=kube-apiserver \\\n'
                '    -o jsonpath=\'{range .items[*]}{.metadata.name}{"\\t"}{.spec.containers[0].command}{"\\n"}{end}\' 2>/dev/null || true)\n'
                '  if [ -z "${APISERVER_POD_LINES}" ]; then\n'
                '    fail "API server: no kube-apiserver pods found"',
                '  APISERVER_POD_LINES=$(kubectl get pods -n kube-system -l component=kube-apiserver \\\n'
                '    -o jsonpath=\'{.items[0].spec.containers[0].command}\' 2>/dev/null || true)\n'
                '  if [ -z "${APISERVER_POD_LINES}" ]; then\n'
                '    fail "API server: no kube-apiserver pods found"',
            ),
            "drop oidc-ca-file check from test-sso.sh": mutate(
                "sso",
                '                  "oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt"; do\n',
                '                  "oidc-groups-prefix=oidc:"; do\n',
            ),
        }

        for name, mutated in mutants.items():
            if mutated == sources:
                print(f"FAIL mutation '{name}' did not apply (anchor drifted)")
                return 1
            if not problems(mutated):
                print(f"FAIL mutation '{name}' was not caught")
                return 1
            print(f"OK mutation caught: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
