#!/usr/bin/env python3
"""Static provider-side S5 checks; mutation mode proves insecure drift is rejected."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
CHART_PATH = ROOT / "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml"
BOOTSTRAP_PATH = ROOT / "scripts/cluster/13-2-narwhal-portal-bindings.sh"


# Commands the portal really issues (narwhal-portal src: valkey.ts, idempotency.ts,
# live-stream.ts, live-k8s-informer.ts, cache-invalidation.ts, health ping/ready check).
PORTAL_ACL_COMMANDS = [
  "auth", "hello", "ping", "info", "select", "client|setname", "client|setinfo",
  "get", "mget", "set", "del", "unlink", "scan", "incr", "multi", "exec", "eval", "pexpire",
  "lpush", "ltrim", "lrange", "publish", "subscribe", "unsubscribe",
]


def validate(chart: str, bootstrap: str) -> None:
  required_chart = [
    "name: narwhal-portal-valkey-tls",
    "name: narwhal-ca-issuer",
    "narwhal-portal-valkey.devtools.svc.cluster.local",
    '            - "0"',
    "--tls-port",
    "--tls-cert-file",
    "--tls-key-file",
    "--tls-auth-clients",
    "--aclfile",
    "name: narwhal-portal-valkey-auth",
    "key: password",
    "name: VALKEY_USERNAME\n              value: portal",
    "name: VALKEY_PASSWORD",
    "name: narwhal-portal-valkey-ingress",
    "app: narwhal-portal-valkey",
    "app: narwhal-portal\n",
    "port: 6379",
  ]
  if not re.search(r'(?m)^\s+- "?--port"?\s*\n\s+- "0"\s*$', chart):
    raise AssertionError("chart must disable the plaintext listener with exactly --port 0")
  missing = [item for item in required_chart if item not in chart]
  if missing:
    raise AssertionError(f"chart missing security contract fragments: {missing}")
  password_env = chart.split("            - name: VALKEY_PASSWORD\n", 1)[1].split(
    "\n            - name:", 1
  )[0]
  if "secretKeyRef:\n                  name: narwhal-portal-valkey-auth\n                  key: password" not in password_env:
    raise AssertionError("VALKEY_PASSWORD must use the dedicated Secret key reference")
  if re.search(r"(?m)^\s+value\s*:", password_env):
    raise AssertionError("VALKEY_PASSWORD must not have an inline value")
  try:
    network_policy = chart.split("name: narwhal-portal-valkey-ingress", 1)[1].split("---", 1)[0]
  except IndexError as exc:
    raise AssertionError("Valkey ingress policy document is missing") from exc
  if "podSelector:\n    matchLabels:\n      app: narwhal-portal-valkey" not in network_policy:
    raise AssertionError("NetworkPolicy must select only the Valkey workload")
  if "from:\n        - podSelector:\n            matchLabels:\n              app: narwhal-portal\n" not in network_policy:
    raise AssertionError("NetworkPolicy must allow only the portal workload")
  if "protocol: TCP\n          port: 6379" not in network_policy:
    raise AssertionError("NetworkPolicy must restrict access to Valkey TCP 6379")
  if network_policy.count("- from:") != 1 or network_policy.count("- podSelector:") != 1 or any(
    broad_rule in network_policy
    for broad_rule in ("ipBlock:", "namespaceSelector:", "    - {}")
  ):
    raise AssertionError("NetworkPolicy contains a broader ingress rule than portal pods")

  required_bootstrap = [
    "openssl rand -hex 32",
    "narwhal-portal-valkey-auth",
    '--from-file=password="${valkey_auth_dir}/password"',
    '--from-file=users.acl="${valkey_auth_dir}/users.acl"',
    "user default off",
    "user portal on >${valkey_password} ~* &live:events:chan",
    'VALKEY_URL="rediss://narwhal-portal-valkey.devtools.svc.cluster.local:6379"',
    'VALKEY_TLS="true"',
    "--owned-key VALKEY_PASSWORD",
    "--owned-key VALKEY_INSECURE_PRODUCTION",
  ]
  missing = [item for item in required_bootstrap if item not in bootstrap]
  if missing:
    raise AssertionError(f"bootstrap missing security contract fragments: {missing}")

  acl_line = next((l for l in bootstrap.splitlines() if l.startswith("user portal on ")), "")
  granted = set(re.findall(r"(?<=\s)\+(\S+)", acl_line))
  absent = [cmd for cmd in PORTAL_ACL_COMMANDS if cmd not in granted]
  if absent:
    raise AssertionError(f"portal ACL missing required commands: {absent}")

  forbidden = [
    "--from-literal=VALKEY_INSECURE_PRODUCTION=",
    "--requirepass",
    "+@all",
    "+all",
    "type: LoadBalancer",
  ]
  present = [item for item in forbidden if item in chart or item in bootstrap]
  if present:
    raise AssertionError(f"insecure Valkey configuration present: {present}")
  if re.search(r"(?m)^\s*--from-literal(?:=|\s+)VALKEY_PASSWORD(?:=|\s+)", bootstrap):
    raise AssertionError("VALKEY_PASSWORD must not be written as a command-line literal")
  if re.search(r"(?m)^\s*VALKEY_PASSWORD\s*[:=]", bootstrap):
    raise AssertionError("VALKEY_PASSWORD must not be assigned an inline literal")

  if "user default off" not in bootstrap or "&live:events:chan" not in bootstrap:
    raise AssertionError("ACL must disable default user and scope the event channel")


def main() -> None:
  chart = CHART_PATH.read_text(encoding="utf-8")
  bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8")
  validate(chart, bootstrap)

  # Verify this check fails closed for representative security regressions.
  mutants = [
    (chart.replace("--tls-port", "--port", 1), bootstrap),
    (chart.replace("app: narwhal-portal\n      ports:", "app: unrelated\n      ports:", 1), bootstrap),
    (chart.replace("  ingress:\n    - from:", "  ingress:\n    - {}\n    - from:", 1), bootstrap),
    (
      chart.replace(
        "        - podSelector:\n            matchLabels:\n              app: narwhal-portal\n",
        "        - podSelector: {}\n        - podSelector:\n            matchLabels:\n              app: narwhal-portal\n",
        1,
      ),
      bootstrap,
    ),
    (
      chart.replace(
        "            - name: VALKEY_PASSWORD\n              valueFrom:\n                secretKeyRef:\n                  name: narwhal-portal-valkey-auth\n                  key: password",
        "            - name: VALKEY_PASSWORD\n              value: inline-secret",
        1,
      ),
      bootstrap,
    ),
    (chart, bootstrap + "\n  --from-literal VALKEY_PASSWORD=inline-secret\n"),
    (chart, bootstrap.replace('VALKEY_TLS="true"', 'VALKEY_TLS="false"', 1)),
    (chart, bootstrap.replace("user default off", "user default on", 1)),
    (chart, bootstrap.replace(" +mget", "", 1)),
    (chart, bootstrap.replace(" +unlink", "", 1)),
    (chart.replace('            - "0"', '            - "1"', 1), bootstrap),
  ]
  for index, (mutant_chart, mutant_bootstrap) in enumerate(mutants, start=1):
    try:
      validate(mutant_chart, mutant_bootstrap)
    except AssertionError:
      continue
    raise AssertionError(f"security regression mutation {index} was accepted")

  print("PASS: Valkey TLS, ACL, secret wiring, ingress scope, and {} negative mutations".format(len(mutants)))


if __name__ == "__main__":
  main()
