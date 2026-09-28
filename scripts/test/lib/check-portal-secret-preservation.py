#!/usr/bin/env python3
"""Behavioral fixture and wiring check for portal Secret key preservation."""

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/cluster/13-2-narwhal-portal-bindings.sh"
HELPER = ROOT / "scripts/cluster/lib-preserve-secret-keys.py"


def wired(source):
  return (
      '"$(dirname "$0")/lib-preserve-secret-keys.py"' in source
      and '"${portal_secret_tmp}/existing.json"' in source
      and '"${portal_secret_tmp}/merged.json"' in source
      and 'kubectl apply -f "${portal_secret_tmp}/merged.json"' in source
      and "--owned-key GITEA_TOKEN" not in source
      and "--owned-key ARGOCD_TOKEN" in source
  )


def main():
  source = SCRIPT.read_text()
  if not wired(source):
    raise SystemExit("13-2 no longer merges foreign Secret keys before apply")

  with tempfile.TemporaryDirectory() as temp_dir:
    temp = Path(temp_dir)
    desired = temp / "desired.json"
    existing = temp / "existing.json"
    output = temp / "merged.json"
    desired.write_text(json.dumps({"data": {"OWNED": "bmV3"}}))
    existing.write_text(json.dumps({"data": {
        "GITEA_URL": "aHR0cHM6Ly9naXRlYS5leGFtcGxl",
        "GITEA_OWNER": "Z2l0ZWEtYWRtaW4=",
        "GITEA_REPO": "bmFyd2hhbC1naXRvcHM=",
        "GITEA_TOKEN": "c2VjcmV0",
        "GITEA_BASE_BRANCH": "bWFpbg==",
        "FOREIGN_KEY": "Zm9yZWlnbg==",
        "ARGOCD_TOKEN": "c3RhbGU=",
    }}))
    subprocess.run([
        "python3", str(HELPER), str(desired), str(existing), str(output),
        "--owned-key", "ARGOCD_TOKEN", "--owned-key", "OPENBAO_TOKEN",
        "--owned-key", "K8S_SA_TOKEN",
    ], check=True)
    merged = json.loads(output.read_text())["data"]
    expected = json.loads(existing.read_text())["data"]
    if any(merged.get(key) != value for key, value in expected.items()
           if key != "ARGOCD_TOKEN"):
      raise SystemExit("fixture foreign Secret keys did not survive the merge")
    if "ARGOCD_TOKEN" in merged:
      raise SystemExit("optional 13-2-owned credential survived after omission")

  if "--mutation-verify" in __import__("sys").argv:
    legacy_create_apply = source.replace(
        'python3 "$(dirname "$0")/lib-preserve-secret-keys.py"',
        'kubectl create secret generic narwhal-portal-secrets --dry-run=client -o yaml | kubectl apply -f -',
    )
    if wired(legacy_create_apply):
      raise SystemExit("mutation was not detected: create|apply still satisfies preservation contract")
    legacy_data = {"OWNED": "bmV3"}
    if any(key in legacy_data for key in expected if key.startswith("GITEA_")):
      raise SystemExit("legacy create|apply mutation unexpectedly retained GITEA keys")


if __name__ == "__main__":
  main()
