#!/usr/bin/env python3
"""R356: Platform-system networking hardening (metallb, apisix, cert-manager).

Pins:
1. MetalLB: frrk8s disabled (L2 only), speaker/controller resources + seccomp RuntimeDefault.
2. APISIX: top-level resources, runAsNonRoot + runAsUser 636, drop ALL, seccomp RuntimeDefault.
3. APISIX Ingress Controller: runAsNonRoot, runAsUser 65532, readOnlyRootFilesystem.
4. cert-manager: controller, webhook, cainjector resources across GitOps and bootstrap.
"""
import glob
import pathlib
import re
import subprocess
import sys
import tempfile
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]


def check_metallb_gitops(text: str) -> None:
    assert re.search(r"frrk8s:\s*\n\s*enabled:\s*false", text), "MetalLB GitOps must set frrk8s.enabled: false"
    assert "seccompProfile:\n              type: RuntimeDefault" in text, "MetalLB GitOps missing seccomp RuntimeDefault"
    assert re.search(r"speaker:\s*\n\s*resources:\s*\n\s*requests:\s*\n\s*cpu:\s*50m", text), "speaker missing cpu request"
    assert re.search(r"controller:\s*\n\s*resources:\s*\n\s*requests:\s*\n\s*cpu:\s*50m", text), "controller missing cpu request"


def check_apisix_gitops(text: str) -> None:
    assert re.search(r"runAsNonRoot:\s*true", text), "APISIX GitOps must set runAsNonRoot: true"
    assert re.search(r"runAsUser:\s*636", text), "APISIX GitOps must set runAsUser: 636"
    assert re.search(r"allowPrivilegeEscalation:\s*false", text), "APISIX GitOps must set allowPrivilegeEscalation: false"
    assert "type: RuntimeDefault" in text, "APISIX GitOps must set RuntimeDefault seccomp"
    assert re.search(r"resources:\s*\n\s*requests:\s*\n\s*cpu:\s*100m\s*\n\s*memory:\s*256Mi", text), "APISIX GitOps must have top-level resources"
    # Ensure resources is NOT under apisix: block
    apisix_block = text[text.index("apisix:\n          enabled: true"):text.index("gateway:\n")]
    assert "resources:" not in apisix_block, "APISIX GitOps must not have misplaced apisix.resources"


def check_cert_manager_gitops(text: str) -> None:
    assert re.search(r"resources:\s*\n\s*requests:\s*\n\s*cpu:\s*25m\s*\n\s*memory:\s*64Mi", text), "cert-manager controller missing resources"
    assert re.search(r"webhook:\s*\n\s*resources:\s*\n\s*requests:\s*\n\s*cpu:\s*25m", text), "cert-manager webhook missing resources"
    assert re.search(r"cainjector:\s*\n\s*resources:\s*\n\s*requests:\s*\n\s*cpu:\s*25m", text), "cert-manager cainjector missing resources"


def check_bootstrap_08_1(script: str) -> None:
    # 1. MetalLB bootstrap flags
    assert "--set frrk8s.enabled=false" in script, "08-1 must disable frrk8s"
    assert "--set speaker.securityContext.seccompProfile.type=RuntimeDefault" in script, "08-1 must set speaker seccomp"
    assert "--set speaker.resources.requests.cpu=50m" in script, "08-1 must set speaker cpu request"
    assert "--set controller.resources.requests.cpu=50m" in script, "08-1 must set controller cpu request"
    assert "--set controller.securityContext.seccompProfile.type=RuntimeDefault" in script, "08-1 must set controller seccomp"

    # 2. APISIX heredoc
    heredoc_start = script.index("cat > /tmp/apisix-values.yaml <<")
    body_start = script.index("\n", heredoc_start) + 1
    body_end = script.index("\nEOF", body_start)
    heredoc = script[body_start:body_end].replace("${POD_NETWORK_CIDR}", "10.0.0.0/16")
    vals = yaml.safe_load(heredoc)
    assert vals.get("securityContext", {}).get("runAsNonRoot") is True, "08-1 apisix values must set runAsNonRoot"
    assert vals.get("securityContext", {}).get("runAsUser") == 636, "08-1 apisix values must set runAsUser: 636"
    assert vals.get("resources", {}).get("requests", {}).get("cpu") == "100m", "08-1 apisix values must set top-level resources"
    assert "resources" not in vals.get("apisix", {}), "08-1 apisix values must not have misplaced apisix.resources"

    # 3. APISIX Ingress Controller bootstrap flags
    assert "--set securityContext.runAsNonRoot=true" in script, "08-1 IC must set runAsNonRoot"
    assert "--set securityContext.runAsUser=65532" in script, "08-1 IC must set runAsUser: 65532"
    assert "--set securityContext.readOnlyRootFilesystem=true" in script, "08-1 IC must set readOnlyRootFilesystem"

    # 4. cert-manager bootstrap flags
    assert "--set resources.requests.cpu=25m" in script, "08-1 cert-manager must set controller cpu request"
    assert "--set webhook.resources.requests.cpu=25m" in script, "08-1 cert-manager must set webhook cpu request"
    assert "--set cainjector.resources.requests.cpu=25m" in script, "08-1 cert-manager must set cainjector cpu request"


def run_mutation_tests(metallb_txt: str, apisix_txt: str, cm_txt: str, script_txt: str) -> int:
    mutations_caught = 0

    # Test MetalLB mutations
    for mutant in [
        metallb_txt.replace("enabled: false", "enabled: true"),
        metallb_txt.replace("RuntimeDefault", "Unconfined"),
        metallb_txt.replace("cpu: 50m", "cpu: 10m"),
    ]:
        try:
            check_metallb_gitops(mutant)
        except AssertionError:
            mutations_caught += 1
        else:
            raise AssertionError(f"MetalLB mutation not caught: {mutant[:100]}")

    # Test APISIX mutations
    for mutant in [
        apisix_txt.replace("runAsNonRoot: true", "runAsNonRoot: false"),
        apisix_txt.replace("runAsUser: 636", "runAsUser: 0"),
        apisix_txt.replace("cpu: 100m", "cpu: 10m"),
        apisix_txt.replace("apisix:\n          enabled: true", "apisix:\n          enabled: true\n          resources:\n            requests:\n              cpu: 100m"),
    ]:
        try:
            check_apisix_gitops(mutant)
        except AssertionError:
            mutations_caught += 1
        else:
            raise AssertionError(f"APISIX mutation not caught: {mutant[:100]}")

    # Test cert-manager mutations
    for mutant in [
        cm_txt.replace("cpu: 25m", "cpu: 5m"),
        cm_txt.replace("webhook:\n          resources:\n            requests:\n              cpu: 25m", "webhook:\n          replicaCount: 2"),
    ]:
        try:
            check_cert_manager_gitops(mutant)
        except AssertionError:
            mutations_caught += 1
        else:
            raise AssertionError(f"cert-manager mutation not caught: {mutant[:100]}")

    # Test 08-1 script mutations
    for mutant in [
        script_txt.replace("--set frrk8s.enabled=false", "--set frrk8s.enabled=true"),
        script_txt.replace("runAsUser: 636", "runAsUser: 0"),
        script_txt.replace("--set securityContext.readOnlyRootFilesystem=true", "# removed ro rootfs"),
        script_txt.replace("--set cainjector.resources.requests.cpu=25m", "# removed cai req"),
    ]:
        try:
            check_bootstrap_08_1(mutant)
        except AssertionError:
            mutations_caught += 1
        else:
            raise AssertionError(f"08-1 script mutation not caught: {mutant[:100]}")

    return mutations_caught


def verify_helm_renders() -> None:
    res = subprocess.run(
        ["helm", "template", "test-apps", "gitops/charts/narwhal-apps", "--set", "baseDomain=example.test"],
        capture_output=True, text=True, check=True, cwd=str(ROOT)
    )
    apps = {d["metadata"]["name"]: d for d in yaml.safe_load_all(res.stdout) if d and d.get("kind") == "Application"}

    # Check that apps rendered with expected valuesObject
    metallb_vals = apps["metallb"]["spec"]["source"]["helm"]["valuesObject"]
    assert metallb_vals["frrk8s"]["enabled"] is False
    assert metallb_vals["speaker"]["resources"]["requests"]["cpu"] == "50m"
    assert metallb_vals["controller"]["resources"]["requests"]["cpu"] == "50m"

    apisix_vals = apps["apisix"]["spec"]["source"]["helm"]["valuesObject"]
    assert apisix_vals["resources"]["requests"]["cpu"] == "100m"
    assert apisix_vals["securityContext"]["runAsUser"] == 636

    cm_vals = apps["cert-manager"]["spec"]["source"]["helm"]["valuesObject"]
    assert cm_vals["resources"]["requests"]["cpu"] == "25m"
    assert cm_vals["webhook"]["resources"]["requests"]["cpu"] == "25m"
    assert cm_vals["cainjector"]["resources"]["requests"]["cpu"] == "25m"

    # If offline bundle is present, verify helm template against upstream charts
    bundle_matches = glob.glob(str(ROOT / "narwhal-airgap-bundle-*/charts/metallb-0.16.1.tgz"))
    if bundle_matches:
        bundle_dir = pathlib.Path(bundle_matches[0]).parent
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            yaml.dump(metallb_vals, f)
            f.flush()
            out = subprocess.run(["helm", "template", "metallb", str(bundle_dir / "metallb-0.16.1.tgz"), "-f", f.name],
                                 capture_output=True, text=True, check=True).stdout
            assert "metallb-frr-k8s" not in out, "offline rendered metallb still contained frr-k8s"
            assert "seccompProfile" in out, "offline rendered metallb missing seccomp"

        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            yaml.dump(apisix_vals, f)
            f.flush()
            out = subprocess.run(["helm", "template", "apisix", str(bundle_dir / "apisix-2.13.0.tgz"), "-f", f.name],
                                 capture_output=True, text=True, check=True).stdout
            assert "runAsUser: 636" in out, "offline rendered apisix missing runAsUser 636"


def main() -> int:
    metallb_path = ROOT / "gitops/charts/narwhal-apps/templates/metallb.yaml"
    apisix_path = ROOT / "gitops/charts/narwhal-apps/templates/apisix.yaml"
    cm_path = ROOT / "gitops/charts/narwhal-apps/templates/cert-manager.yaml"
    script_path = ROOT / "scripts/cluster/08-1-networking.sh"

    metallb_txt = metallb_path.read_text()
    apisix_txt = apisix_path.read_text()
    cm_txt = cm_path.read_text()
    script_txt = script_path.read_text()

    check_metallb_gitops(metallb_txt)
    check_apisix_gitops(apisix_txt)
    check_cert_manager_gitops(cm_txt)
    check_bootstrap_08_1(script_txt)

    mutations = run_mutation_tests(metallb_txt, apisix_txt, cm_txt, script_txt)
    verify_helm_renders()

    print(f"PASS: Platform-system networking hardening verified ({mutations} mutations caught)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
