#!/usr/bin/env python3
"""R270: SeaweedFS S3 authentication contract (D-s3auth), run from the repo root.

* seaweedfs: s3.enableAuth + existingConfigSecret seaweedfs-s3-config in the GitOps chart AND the 08-4
  helm install; the embedded filer S3 is disabled in both (the seaweedfs-filer Service would publish a
  second, unauthenticated :8333).
* loki/tempo: no inline S3 key; the key comes from the client Secret via envFrom and expand-env.
* narwhal-db.yaml carries no cnpg-s3-credentials Secret (14 re-applies that file and reverted the keys).
* 14 aborts when the Secret is missing; 07 and 08-4 call ensure_s3_identities; no admin/admin default left.
* Velero in 08-4 reads velero-s3-credentials (existingSecret), never inline secretContents.
Use --mutation-verify to prove every assertion fails when its contract is broken."""
import re
import sys
import yaml

FILES = {
    "sw": "gitops/charts/narwhal-apps/templates/seaweedfs.yaml",
    "loki": "gitops/charts/narwhal-apps/templates/loki.yaml",
    "tempo": "gitops/charts/narwhal-apps/templates/tempo.yaml",
    "db": "gitops/resources/narwhal-db.yaml",
    "s08": "scripts/cluster/08-4-storage.sh",
    "s07": "scripts/cluster/07-cnpg.sh",
    "s14": "scripts/cluster/14-gitops-bootstrap.sh",
    "lib": "scripts/common/lib.sh",
}


def values(text):
    return yaml.safe_load(text)["spec"]["source"]["helm"]["valuesObject"]


def verify(t):
    sw = values(t["sw"])
    assert sw["s3"]["enableAuth"] is True, "chart s3.enableAuth"
    assert sw["s3"]["existingConfigSecret"] == "seaweedfs-s3-config", "chart existingConfigSecret"
    assert sw["filer"]["s3"]["enabled"] is False, "chart filer.s3 must be disabled"
    s08 = t["s08"]
    for flag in ("--set s3.enableAuth=true", "--set s3.existingConfigSecret=seaweedfs-s3-config", "--set filer.s3.enabled=false"):
        assert flag in s08, f"08-4 {flag}"
    assert "--set filer.s3.enabled=true" not in s08, "08-4 must not enable filer S3"
    assert s08.index("ensure_s3_identities ||") < s08.index("helm upgrade --install seaweedfs"), "identities before the S3 server"
    assert "rollout restart deployment/seaweedfs-s3" in s08 and "S3_IDENTITIES_CHANGED" in s08, "08-4 restarts S3 on changed keys"
    assert "existingSecret: velero-s3-credentials" in s08 and "secretContents" not in s08, "08-4 velero reads the Secret"
    assert "resolve_s3_credentials" not in s08 + t["s07"] + t["lib"], "old default-admin resolver is gone"
    assert "ensure_s3_identities ||" in t["s07"], "07 ensures identities"

    loki = values(t["loki"])
    ls3 = loki["loki"]["storage"]["s3"]
    assert ls3["accessKeyId"] == "${S3_ACCESS_KEY_ID}" and ls3["secretAccessKey"] == "${S3_SECRET_ACCESS_KEY}", "loki keys via env"
    assert {"secretRef": {"name": "loki-s3-credentials"}} in loki["global"]["extraEnvFrom"], "loki envFrom"
    tempo = values(t["tempo"])
    ts3 = tempo["tempo"]["storage"]["trace"]["s3"]
    assert ts3["access_key"] == "${S3_ACCESS_KEY_ID}" and ts3["secret_key"] == "${S3_SECRET_ACCESS_KEY}", "tempo keys via env"
    assert tempo["tempo"]["extraArgs"]["config.expand-env"] == "true", "tempo expand-env"
    assert {"secretRef": {"name": "tempo-s3-credentials"}} in tempo["tempo"]["extraEnvFrom"], "tempo envFrom"

    for d in yaml.safe_load_all(t["db"]):
        assert not (d and d.get("kind") == "Secret"), "narwhal-db.yaml must not own any Secret"
    assert not re.search(r"^\s*(ACCESS_KEY_ID|ACCESS_SECRET_KEY):", t["db"], re.M), "no inline cnpg keys in GitOps"
    assert "get secret cnpg-s3-credentials -n database" in t["s14"] and \
        t["s14"].index("get secret cnpg-s3-credentials") < t["s14"].index("kubectl apply -f /home/vagrant/configs/gitops/resources/narwhal-db.yaml"), "14 guards the Secret"
    for name in ("loki", "tempo", "sw"):
        assert not re.search(r"(accessKeyId|secretAccessKey|access_key|secret_key):\s*admin\b", t[name]), f"{name} inline admin"


def load():
    return {k: open(v).read() for k, v in FILES.items()}


def mutations():
    return [
        ("sw", "enableAuth: true", "enableAuth: false"),
        ("sw", "existingConfigSecret: seaweedfs-s3-config", "existingConfigSecret: other"),
        ("sw", "          s3:\n            enabled: false\n", "          s3:\n            enabled: true\n"),
        ("s08", "--set s3.enableAuth=true", "--set s3.enableAuth=false"),
        ("s08", "--set filer.s3.enabled=false", "--set filer.s3.enabled=true"),
        ("s08", "existingSecret: velero-s3-credentials", "secretContents:\n    cloud: x"),
        ("s08", "rollout restart deployment/seaweedfs-s3", "rollout status deployment/seaweedfs-s3"),
        ("s07", "ensure_s3_identities ||", "true ||"),
        ("loki", "accessKeyId: ${S3_ACCESS_KEY_ID}", "accessKeyId: admin"),
        ("loki", "name: loki-s3-credentials", "name: other"),
        ("tempo", "secret_key: ${S3_SECRET_ACCESS_KEY}", "secret_key: admin"),
        ("tempo", 'config.expand-env: "true"', 'config.expand-env: "false"'),
        ("db", "apiVersion: postgresql.cnpg.io/v1", "apiVersion: v1\nkind: Secret\nmetadata:\n  name: cnpg-s3-credentials\nstringData: {}\n---\napiVersion: postgresql.cnpg.io/v1"),
        ("s14", "get secret cnpg-s3-credentials -n database", "get secret x -n database"),
    ]


if __name__ == "__main__":
    base = load()
    verify(base)
    if "--mutation-verify" in sys.argv:
        for key, old, new in mutations():
            assert old in base[key], f"mutation anchor missing in {key}: {old!r}"
            m = dict(base)
            m[key] = base[key].replace(old, new, 1)
            try:
                verify(m)
            except (AssertionError, KeyError, TypeError):
                continue
            sys.exit(f"mutation survived: {key}: {old!r}")
    print("OK")
