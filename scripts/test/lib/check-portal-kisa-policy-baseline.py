#!/usr/bin/env python3
"""R399: Portal KISA Kyverno policy baseline digest sync & canonicalization integrity.

Verifies offline that:
1. `node portal/scripts/gen-kisa-policy-baseline.mjs --check` succeeds against
   `gitops/resources/kyverno-policies.yaml` and `portal/src/data/kisa-policy-baseline.json`.
2. Behavioral Node probe proves:
   - Stripping only documented Kyverno 1.18 admission defaults leaves computeKyvernoSpecDigest
     unchanged.
   - Mutating security-relevant fields (admission=false, match, exclude, preconditions, deny,
     CEL, imageReferences, required, non-sha256 signatureAlgorithm, overrides) changes the digest
     or flags specHasOverrides=true.
   - validateBaseline rejects tampered specSha256, fake enforceEligible=true on Audit/limited
     verify-image-signatures, manifest drift, and missing policies.
3. Python independently cross-checks kisa-policy-baseline.json against kyverno-policies.yaml.
4. `--mutation-verify` proves tampered baseline / canonicalizer / R399 wiring are rejected.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "gen": ROOT / "portal/scripts/gen-kisa-policy-baseline.mjs",
    "canon": ROOT / "portal/src/lib/kisa-policy-canonical.mjs",
    "baseline": ROOT / "portal/src/data/kisa-policy-baseline.json",
    "manifest": ROOT / "gitops/resources/kyverno-policies.yaml",
    "kisa": ROOT / "portal/src/lib/kisa.ts",
    "checks": ROOT / "portal/src/lib/kisa-checks.ts",
    "checks_ops": ROOT / "portal/src/lib/kisa-checks-ops.ts",
    "test": ROOT / "portal/src/lib/kisa-live-controls.test.ts",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def check_live_generator_cli() -> list[str]:
    res = subprocess.run(
        ["node", str(FILES["gen"]), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return [f"gen-kisa-policy-baseline.mjs --check failed (rc={res.returncode}): {res.stderr.strip()} {res.stdout.strip()}"]

    probe = r"""
import { readFileSync } from "node:fs";
import yaml from "./portal/node_modules/js-yaml/dist/js-yaml.mjs";
import {
  buildPolicyBaselineFromManifest,
  buildNetworkPolicyBaselineFromSources,
  validateBaseline,
  MANIFEST_PATH,
  OUT_JSON,
} from "./portal/scripts/gen-kisa-policy-baseline.mjs";
import {
  computeKyvernoPolicyDigest,
  computeKyvernoSpecDigest,
  computeNetworkPolicyDigest,
  computeNetworkPolicySpecDigest,
  specHasOverrides,
} from "./portal/src/lib/kisa-policy-canonical.mjs";

const manifestText = readFileSync(MANIFEST_PATH, "utf8");
const expected = buildPolicyBaselineFromManifest(manifestText);
const actual = JSON.parse(readFileSync(OUT_JSON, "utf8"));
if (validateBaseline(actual, expected).length !== 0) {
  console.error("baseline failed validateBaseline");
  process.exit(1);
}

const docs = yaml.loadAll(manifestText).filter((d) => d && d.kind === "ClusterPolicy");
const latest = docs.find((d) => d.metadata?.name === "disallow-latest-tag");
const verify = docs.find((d) => d.metadata?.name === "verify-image-signatures");
if (!latest || !verify) {
  console.error("missing policies in manifest");
  process.exit(1);
}

const clone = (o) => JSON.parse(JSON.stringify(o));

// 1. Documented Kyverno admission defaults strip to exact same digest
const latestWithDefaults = clone(latest.spec);
latestWithDefaults.admission = true;
latestWithDefaults.emitWarning = false;
latestWithDefaults.rules[0].skipBackgroundRequests = true;
latestWithDefaults.rules[0].validate.allowExistingViolations = true;
if (computeKyvernoSpecDigest(latestWithDefaults) !== actual.policies["disallow-latest-tag"].specSha256) {
  console.error("Kyverno admission defaults altered disallow-latest-tag digest");
  process.exit(1);
}

const verifyWithDefaults = clone(verify.spec);
verifyWithDefaults.admission = true;
verifyWithDefaults.emitWarning = false;
verifyWithDefaults.rules[0].skipBackgroundRequests = true;
verifyWithDefaults.rules[0].verifyImages[0].useCache = true;
verifyWithDefaults.rules[0].verifyImages[0].attestors[0].entries[0].signatureAlgorithm = "sha256";
verifyWithDefaults.rules[0].verifyImages[0].attestors[0].entries[0].keys.signatureAlgorithm = "sha256";
if (computeKyvernoSpecDigest(verifyWithDefaults) !== actual.policies["verify-image-signatures"].specSha256) {
  console.error("Kyverno admission defaults altered verify-image-signatures digest");
  process.exit(1);
}

// 2. Security-relevant mutations MUST change digest or flag overrides
const latestAlgoMut = clone(latest.spec);
latestAlgoMut.admission = false;
const latestExclude = clone(latest.spec);
latestExclude.rules[0].exclude = { any: [{ resources: { namespaces: ["*"] } }] };
const latestMatch = clone(latest.spec);
latestMatch.rules[0].match = { any: [{ resources: { kinds: ["Pod"], namespaces: ["dev"] } }] };
const latestPrecond = clone(latest.spec);
latestPrecond.rules[0].preconditions = { all: [{ key: "a", operator: "Equals", value: "b" }] };
const latestRuleDisable = clone(latest.spec);
latestRuleDisable.rules[0].validationFailureAction = "Audit";

for (const [label, mutatedSpec] of [
  ["admission=false", latestAlgoMut],
  ["exclude added", latestExclude],
  ["match narrowed", latestMatch],
  ["precondition added", latestPrecond],
  ["rule override", latestRuleDisable],
]) {
  const digDiff = computeKyvernoSpecDigest(mutatedSpec) !== actual.policies["disallow-latest-tag"].specSha256;
  const ovDiff = specHasOverrides(mutatedSpec);
  if (!digDiff && !ovDiff) {
    console.error("security mutation was not detected: " + label);
    process.exit(1);
  }
}

const verifyAlgo = clone(verify.spec);
verifyAlgo.rules[0].verifyImages[0].attestors[0].entries[0].signatureAlgorithm = "sha512";
if (computeKyvernoSpecDigest(verifyAlgo) === actual.policies["verify-image-signatures"].specSha256) {
  console.error("non-default signatureAlgorithm=sha512 was stripped");
  process.exit(1);
}

// 3. validateBaseline drift/tamper checks
const fakeEnforce = clone(actual);
fakeEnforce.policies["verify-image-signatures"].enforceEligible = true;
const badDigest = clone(actual);
badDigest.policies["disallow-latest-tag"].specSha256 = "0".repeat(64);
const driftedManifest = buildPolicyBaselineFromManifest(
  manifestText.replace("name: validate-image-tag", "name: validate-image-tag-mutated"),
);
const missingPol = clone(actual);
delete missingPol.policies["verify-image-signatures"];

for (const [label, candidate, exp] of [
  ["fake enforceEligible", fakeEnforce, expected],
  ["tampered digest", badDigest, expected],
  ["manifest drift", actual, driftedManifest],
  ["missing baseline policy", missingPol, expected],
]) {
  if (validateBaseline(candidate, exp).length === 0) {
    console.error("validateBaseline failed to reject: " + label);
    process.exit(1);
  }
}

// 4. NetworkPolicy / CiliumNetworkPolicy canonicalization & drift checks
const iamDenySpecLive = { podSelector: {}, policyTypes: ["Ingress"] }; // API server omits empty ingress: []
const iamDenyDigest = computeNetworkPolicySpecDigest("NetworkPolicy", iamDenySpecLive);
if (iamDenyDigest !== actual.networkPolicies["NetworkPolicy/iam/iam-default-deny-ingress"].specSha256) {
  console.error("Empty NetworkPolicy ingress: [] stripping mismatched live API server readback");
  process.exit(1);
}
const tuningLive = { podSelector: { matchLabels: { "app.kubernetes.io/name": "narwhal-tuning" } }, policyTypes: ["Ingress", "Egress"] };
if (computeNetworkPolicySpecDigest("NetworkPolicy", tuningLive) !== actual.networkPolicies["NetworkPolicy/devtools/tuning-job-network-policy"].specSha256) {
  console.error("Empty NetworkPolicy ingress/egress: [] stripping mismatched tuning-job-network-policy");
  process.exit(1);
}
const iamAllowAllMutated = { podSelector: {}, policyTypes: ["Ingress"], ingress: [{}] };
if (computeNetworkPolicySpecDigest("NetworkPolicy", iamAllowAllMutated) === iamDenyDigest) {
  console.error("Non-empty NetworkPolicy ingress:[{}] produced same digest as deny-all");
  process.exit(1);
}
const badNetDigest = clone(actual);
badNetDigest.networkPolicies["NetworkPolicy/platform-system/apisix-etcd-ingress-policy"].specSha256 = "f".repeat(64);
if (validateBaseline(badNetDigest, expected).length === 0) {
  console.error("validateBaseline failed to reject tampered networkPolicy digest");
  process.exit(1);
}

// 5. CNP/CCNP {spec, specs} + unknown top-level field canonicalization + empty podSelector generator assertion
const cnpSpecOnly = {
  kind: "CiliumNetworkPolicy",
  metadata: { name: "x", namespace: "platform-system" },
  spec: { endpointSelector: { matchLabels: { app: "other" } } },
};
const cnpWithSpecs = {
  ...cnpSpecOnly,
  specs: [{ endpointSelector: { matchLabels: { app: "apisix-etcd" } }, ingress: [{ fromEntities: ["all"] }] }],
};
if (computeNetworkPolicyDigest("CiliumNetworkPolicy", cnpSpecOnly) === computeNetworkPolicyDigest("CiliumNetworkPolicy", cnpWithSpecs)) {
  console.error("CiliumNetworkPolicy specs[] field was ignored by canonicalizer");
  process.exit(1);
}

const ccnpFromYaml = yaml.load(readFileSync("./gitops/resources/ambient-kubelet-probes-ccnp.yaml", "utf8"));
const ccnpBaselineEntry = actual.clusterwidePolicies?.["CiliumClusterwideNetworkPolicy/allow-ambient-kubelet-probes"];
if (!ccnpBaselineEntry || computeNetworkPolicyDigest("CiliumClusterwideNetworkPolicy", ccnpFromYaml) !== ccnpBaselineEntry.specSha256) {
  console.error("CCNP allow-ambient-kubelet-probes digest mismatch");
  process.exit(1);
}
const ccnpWithSpecs = { ...clone(ccnpFromYaml), specs: [{ endpointSelector: {}, ingress: [{ fromEntities: ["all"] }] }] };
if (computeNetworkPolicyDigest("CiliumClusterwideNetworkPolicy", ccnpWithSpecs) === ccnpBaselineEntry.specSha256) {
  console.error("CiliumClusterwideNetworkPolicy specs[] field was ignored by canonicalizer");
  process.exit(1);
}

// Non-semantic fields (metadata/status/apiVersion/kind) do not alter digest; ANY unknown top-level field MUST alter digest
const ccnpReorderedMeta = { ...clone(ccnpFromYaml), metadata: { name: "other", resourceVersion: "99" }, status: { conditions: [] } };
if (computeNetworkPolicyDigest("CiliumClusterwideNetworkPolicy", ccnpReorderedMeta) !== ccnpBaselineEntry.specSha256) {
  console.error("Non-semantic metadata/status altered CCNP digest");
  process.exit(1);
}
for (const [kind, obj] of [
  ["NetworkPolicy", { spec: iamDenySpecLive }],
  ["CiliumNetworkPolicy", cnpSpecOnly],
  ["CiliumClusterwideNetworkPolicy", ccnpFromYaml],
  ["ClusterPolicy", latest],
]) {
  const baseDig = computeNetworkPolicyDigest(kind, obj);
  const withFutureField = { ...clone(obj), futurePolicyField: { allow: true } };
  if (computeNetworkPolicyDigest(kind, withFutureField) === baseDig) {
    console.error(`Unknown top-level policy field was ignored for ${kind}`);
    process.exit(1);
  }
}

// Empty podSelector assertion in generator
let threwOnNonEmptyDefaultDeny = false;
try {
  buildNetworkPolicyBaselineFromSources([
    {
      path: "test/non-empty-dd.yaml",
      templated: false,
      text: [
        "apiVersion: networking.k8s.io/v1",
        "kind: NetworkPolicy",
        "metadata: { name: iam-default-deny-ingress, namespace: iam }",
        "spec: { podSelector: { matchLabels: { app: only-one } }, policyTypes: [Ingress], ingress: [] }",
        "---",
        readFileSync("./gitops/resources/database-ingress-policy.yaml", "utf8"),
        "---",
        readFileSync("./gitops/resources/monitoring-ingress-policy.yaml", "utf8"),
        "---",
        readFileSync("./gitops/resources/storage-ingress-policy.yaml", "utf8"),
        "---",
        readFileSync("./gitops/resources/apisix-etcd-ingress-policy.yaml", "utf8"),
      ].join("\\n"),
    },
  ]);
} catch {
  threwOnNonEmptyDefaultDeny = true;
}
if (!threwOnNonEmptyDefaultDeny) {
  console.error("Generator failed to reject non-empty podSelector on NET-01 default-deny");
  process.exit(1);
}
"""
    res2 = subprocess.run(
        ["node", "--input-type=module", "-e", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if res2.returncode != 0:
        return [f"kisa-policy-baseline behavioral test failed: {res2.stderr.strip()} {res2.stdout.strip()}"]
    return []


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    try:
        base = json.loads(texts["baseline"])
    except Exception as exc:
        return [f"kisa-policy-baseline.json invalid JSON: {exc}"]

    if base.get("schemaVersion") != 2:
        errors.append(f"baseline schemaVersion must be 2, got {base.get('schemaVersion')!r}")
    if base.get("sourceManifest") != "gitops/resources/kyverno-policies.yaml":
        errors.append(f"baseline sourceManifest must be gitops/resources/kyverno-policies.yaml, got {base.get('sourceManifest')!r}")

    defaults = base.get("normalizedOutServerDefaults") or []
    for req_default in (
        "spec.admission (when true)",
        "spec.emitWarning (when false)",
        "spec.rules[].skipBackgroundRequests (when true)",
        "spec.rules[].validate.allowExistingViolations (when true)",
        "spec.rules[].verifyImages[].useCache (when true)",
        "spec.rules[].verifyImages[].attestors[].entries[].signatureAlgorithm (when sha256)",
    ):
        if req_default not in defaults:
            errors.append(f"baseline normalizedOutServerDefaults missing {req_default!r}")

    net_defaults = base.get("normalizedOutNetworkPolicyDefaults") or []
    for req_np_default in (
        "NetworkPolicy spec.ingress (when empty array [])",
        "NetworkPolicy spec.egress (when empty array [])",
        "CiliumNetworkPolicy/CiliumClusterwideNetworkPolicy specs (when empty array [])",
    ):
        if req_np_default not in net_defaults:
            errors.append(f"baseline normalizedOutNetworkPolicyDefaults missing {req_np_default!r}")

    net_pols = base.get("networkPolicies") or {}
    if len(net_pols) < 33:
        errors.append(f"expected >=33 baselined NetworkPolicy/CiliumNetworkPolicy entries, got {len(net_pols)}")
    ccnp_pols = base.get("clusterwidePolicies") or {}
    if "CiliumClusterwideNetworkPolicy/allow-ambient-kubelet-probes" not in ccnp_pols:
        errors.append("baseline clusterwidePolicies missing CiliumClusterwideNetworkPolicy/allow-ambient-kubelet-probes")
    for key, entry in ccnp_pols.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("specSha256") or "")):
            errors.append(f"{key}: invalid clusterwidePolicy specSha256 {entry.get('specSha256')!r}")
    for ns, dd_key in (base.get("net01DefaultDenyByNamespace") or {}).items():
        if ns != "devtools" and (not dd_key or dd_key not in net_pols):
            errors.append(f"missing net01DefaultDenyByNamespace entry for {ns}: {dd_key!r}")
    etcd_key = base.get("etcd02PolicyKey")
    if etcd_key != "NetworkPolicy/platform-system/apisix-etcd-ingress-policy" or etcd_key not in net_pols:
        errors.append(f"missing or invalid etcd02PolicyKey: {etcd_key!r}")
    etcd_wl = base.get("etcd02Workload") or {}
    if etcd_wl.get("kind") != "Deployment" or etcd_wl.get("namespace") != "platform-system" or etcd_wl.get("name") != "apisix-etcd":
        errors.append(f"missing or invalid etcd02Workload: {etcd_wl!r}")
    for key, entry in net_pols.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("specSha256") or "")):
            errors.append(f"{key}: invalid networkPolicy specSha256 {entry.get('specSha256')!r}")

    try:
        manifest_docs = [d for d in yaml.safe_load_all(texts["manifest"]) if isinstance(d, dict) and d.get("kind") == "ClusterPolicy"]
    except yaml.YAMLError as exc:
        return [f"kyverno-policies.yaml YAML parse failed: {exc}"]

    by_name = {d["metadata"]["name"]: d for d in manifest_docs if (d.get("metadata") or {}).get("name")}
    policies = base.get("policies") or {}

    for pname, role in (("disallow-latest-tag", "latest-tag"), ("verify-image-signatures", "verify-images")):
        doc = by_name.get(pname)
        if not doc:
            errors.append(f"kyverno-policies.yaml missing ClusterPolicy {pname}")
            continue
        entry = policies.get(pname)
        if not entry:
            errors.append(f"kisa-policy-baseline.json missing {pname}")
            continue
        if entry.get("role") != role:
            errors.append(f"{pname}: expected role={role}, got {entry.get('role')!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("specSha256") or "")):
            errors.append(f"{pname}: invalid specSha256 {entry.get('specSha256')!r}")

        manifest_action = (doc.get("spec") or {}).get("validationFailureAction", "Audit")
        if entry.get("validationFailureAction") != manifest_action:
            errors.append(f"{pname}: validationFailureAction={entry.get('validationFailureAction')!r} != manifest {manifest_action!r}")

    verify_entry = policies.get("verify-image-signatures") or {}
    if verify_entry.get("validationFailureAction") != "Enforce" and verify_entry.get("enforceEligible") is not False:
        errors.append("verify-image-signatures cannot have enforceEligible=True while Audit/limited")
    if verify_entry.get("enforceEligible") is True:
        lims = verify_entry.get("knownLimitations") or []
        if lims:
            errors.append(f"verify-image-signatures enforceEligible=True cannot have knownLimitations {lims}")

    for needle in (
        "KYVERNO_ADMISSION_DEFAULTS",
        "NETWORK_POLICY_DEFAULTS",
        "NON_SEMANTIC_TOP_LEVEL_FIELDS",
        "POLICY_TOP_LEVEL_ALLOWLIST_BY_KIND",
        "if (NON_SEMANTIC_TOP_LEVEL_SET.has(key)) continue",
        "for (const key of Object.keys(policyObj))",
        "if (out.admission === true) delete out.admission",
        "if (out.signatureAlgorithm === \"sha256\") delete out.signatureAlgorithm",
        "if (Array.isArray(out.ingress) && out.ingress.length === 0) delete out.ingress",
        "if (Array.isArray(out.egress) && out.egress.length === 0) delete out.egress",
        "if (out.specs === null || out.specs === undefined || (Array.isArray(out.specs) && out.specs.length === 0))",
        "export function canonicalizePolicyObject(",
        "export function computeNetworkPolicyDigest(",
        "export function computeKyvernoPolicyDigest(",
        "export function computeKyvernoSpecDigest(",
        "export function computeNetworkPolicySpecDigest(",
        "export function specHasOverrides(",
    ):
        if needle not in texts["canon"]:
            errors.append(f"kisa-policy-canonical.mjs missing {needle!r}")

    if 'if (doc?.kind !== "NetworkPolicy" || !isEmptyPodSelector(doc?.spec?.podSelector))' not in texts["gen"]:
        errors.append("gen-kisa-policy-baseline.mjs missing isEmptyPodSelector assertion for NET-01 default-deny")

    for needle in (
        "export const KISA_POLICY_BASELINE: KisaPolicyBaseline",
        "checkImg02(baseline: KisaPolicyBaseline = KISA_POLICY_BASELINE)",
        "computeKyvernoPolicyDigest",
        "specHasOverrides",
        "isPolicyReady",
    ):
        if needle not in texts["checks_ops"]:
            errors.append(f"kisa-checks-ops.ts missing {needle!r}")

    if "computeNetworkPolicyDigest" not in texts["kisa"] or "baseline.net01DefaultDenyByNamespace" not in texts["kisa"]:
        errors.append("kisa.ts must verify NET-01 against kisa-policy-baseline.json networkPolicies")
    if "computeNetworkPolicyDigest" not in texts["checks"] or "baseline.etcd02PolicyKey" not in texts["checks"]:
        errors.append("kisa-checks.ts must verify ETCD-02 against kisa-policy-baseline.json networkPolicies")

    if "reproduction (R5):" not in texts["test"]:
        errors.append("kisa-live-controls.test.ts missing R5 IMG-02 closed-world tests")

    if "check R399 " not in texts["reg"] or "check-portal-kisa-policy-baseline.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R399 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("mark verify-image-signatures enforceEligible=true while Audit", "baseline", '"required": false,\n      "enforceEligible": false,', '"required": false,\n      "enforceEligible": true,'),
    ("tamper disallow-latest-tag specSha256 format", "baseline", '"role": "latest-tag",\n      "admissionRelevant": true,\n      "specSha256": "', '"role": "latest-tag",\n      "admissionRelevant": true,\n      "specSha256": "invalid'),
    ("drop admission=true guard in canonicalizer (would strip admission=false)", "canon", "if (out.admission === true) delete out.admission", "delete out.admission"),
    ("drop sha256 value guard in signatureAlgorithm stripper", "canon", 'if (out.signatureAlgorithm === "sha256") delete out.signatureAlgorithm', "delete out.signatureAlgorithm"),
    ("drop empty-array guard on NetworkPolicy ingress stripper", "canon", "if (Array.isArray(out.ingress) && out.ingress.length === 0) delete out.ingress", "delete out.ingress"),
    ("ignore policy-bearing fields (specs/future fields) by iterating only spec in canonicalizePolicyObject", "canon", "for (const key of Object.keys(policyObj))", 'for (const key of ["spec"])'),
    ("drop empty-array guard on Cilium specs[] stripper", "canon", "if (out.specs === null || out.specs === undefined || (Array.isArray(out.specs) && out.specs.length === 0))", "if (true)"),
    ("drop isEmptyPodSelector assertion in baseline generator", "gen", "if (doc?.kind !== \"NetworkPolicy\" || !isEmptyPodSelector(doc?.spec?.podSelector))", "if (false)"),
    ("tamper NetworkPolicy baseline digest", "baseline", '"name": "apisix-etcd-ingress-policy",\n      "sourceManifest": "gitops/resources/apisix-etcd-ingress-policy.yaml",\n      "specSha256": "', '"name": "apisix-etcd-ingress-policy",\n      "sourceManifest": "gitops/resources/apisix-etcd-ingress-policy.yaml",\n      "specSha256": "invalid'),
    ("tamper CCNP baseline digest", "baseline", '"name": "allow-ambient-kubelet-probes",\n      "sourceManifest": "gitops/resources/ambient-kubelet-probes-ccnp.yaml",\n      "specSha256": "', '"name": "allow-ambient-kubelet-probes",\n      "sourceManifest": "gitops/resources/ambient-kubelet-probes-ccnp.yaml",\n      "specSha256": "invalid'),
    ("tamper etcd02Workload name in baseline", "baseline", '"name": "apisix-etcd"\n  },', '"name": "etcd-v2"\n  },'),
    ("drop KISA_POLICY_BASELINE default in checkImg02", "checks_ops", "checkImg02(baseline: KisaPolicyBaseline = KISA_POLICY_BASELINE)", "checkImg02(baseline: KisaPolicyBaseline = { schemaVersion: 2, sourceManifest: '', normalizedOutServerDefaults: [], policies: {} })"),
    ("drop R399 wiring in regression-check-kakao.sh", "reg", "check R399 ", "check R9999 "),
]


def main() -> int:
    cli_errors = check_live_generator_cli()
    if cli_errors:
        print("\n".join(cli_errors), file=sys.stderr)
        return 1

    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv[1:]:
        for name, key, old, new in MUTATIONS:
            if old not in texts[key]:
                print(f"FAIL: mutation target missing for {name} ({key})", file=sys.stderr)
                return 1
            mutated = copy.deepcopy(texts)
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R399 portal KISA Kyverno policy baseline sync ({len(MUTATIONS)} mutations + 16 behavioral checks rejected)")
        return 0

    print("PASS: R399 portal KISA Kyverno policy baseline sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
