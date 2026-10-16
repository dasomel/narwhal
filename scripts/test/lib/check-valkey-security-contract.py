#!/usr/bin/env python3
"""Static provider-side S5 checks; mutation mode proves insecure drift is rejected."""

from pathlib import Path
import re

import yaml

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


def _valkey_args(chart: str) -> tuple:
  """Parse the effective Valkey argv (command + args) into {flag: [values]}; duplicates are kept
  so they can be rejected (Valkey applies the LAST occurrence, so a trailing --port would win).
  Returns (flags, container, pod_spec)."""
  docs = [d for d in yaml.safe_load_all(chart) if isinstance(d, dict)]
  deploys = [
    d for d in docs
    if d.get("kind") == "Deployment" and d["metadata"]["name"] == "narwhal-portal-valkey"
  ]
  if len(deploys) != 1:
    raise AssertionError("exactly one narwhal-portal-valkey Deployment is required")
  pod = deploys[0]["spec"]["template"]["spec"]
  containers = [c for c in pod["containers"] if c["name"] == "valkey"]
  if len(containers) != 1:
    raise AssertionError("exactly one valkey container is required")
  tokens = [str(t) for t in containers[0].get("command", [])] + [
    str(t) for t in containers[0].get("args", [])
  ]
  if not tokens or tokens[0] != "valkey-server":
    raise AssertionError("valkey container must run valkey-server")
  flags: dict = {}
  index = 1
  while index < len(tokens):
    if not tokens[index].startswith("--"):
      raise AssertionError(f"unexpected positional valkey-server argument: {tokens[index]!r}")
    values = []
    index += 1
    while index < len(tokens) and not tokens[index].startswith("--"):
      values.append(tokens[index])
      index += 1
    flags.setdefault(tokens[index - len(values) - 1], []).append(values)
  return flags, containers[0], pod


def validate_structured(chart: str, bootstrap: str) -> None:
  """Parsed (not substring) checks: R247 hardening after the independent review."""
  flags, container, pod = _valkey_args(chart)
  duplicated = sorted(f for f, v in flags.items() if len(v) != 1)
  if duplicated:
    raise AssertionError(f"valkey flags must appear exactly once (last wins): {duplicated}")
  expected = {
    "--port": ["0"], "--tls-port": ["6379"], "--tls-auth-clients": ["no"],
    "--tls-cert-file": ["/etc/valkey/tls/tls.crt"], "--tls-key-file": ["/etc/valkey/tls/tls.key"],
    "--aclfile": ["/etc/valkey/auth/users.acl"],
  }
  for flag, value in expected.items():
    if flags.get(flag, [None])[0] != value:
      raise AssertionError(f"valkey {flag} must be exactly {value}, got {flags.get(flag)}")
  banned = sorted({"--requirepass", "--protected-mode", "--user", "--include", "--acl-pubsub-default"} & set(flags))
  if banned:
    raise AssertionError(f"valkey flags bypass the ACL file: {banned}")
  unknown = sorted(set(flags) - set(expected) - {"--save", "--appendonly"})
  if unknown:
    raise AssertionError(f"unreviewed valkey flags (allowlist only): {unknown}")
  if container.get("args"):
    raise AssertionError("valkey container must not use args beyond the reviewed command")
  if container.get("env"):
    raise AssertionError("valkey container must not set env entries (config via env bypasses flags)")
  launchers = [
    c["name"] for c in pod["containers"] + pod.get("initContainers", [])
    if "valkey" in str(c.get("image", "")) or "valkey-server" in " ".join(
      str(t) for t in c.get("command", []) + c.get("args", []))
  ]
  if launchers != ["valkey"]:
    raise AssertionError(f"exactly one reviewed valkey-server launcher is allowed, got {launchers}")
  volumes = {v["name"]: v for v in pod.get("volumes", [])}
  auth = volumes.get("valkey-auth", {}).get("secret", {})
  if auth.get("secretName") != "narwhal-portal-valkey-auth" or auth.get("items") != [
    {"key": "users.acl", "path": "users.acl"}
  ]:
    raise AssertionError("valkey-auth volume must project only key users.acl at path users.acl")
  mounts = {m["name"]: m["mountPath"] for m in container.get("volumeMounts", [])}
  if mounts.get("valkey-auth") != "/etc/valkey/auth":
    raise AssertionError("valkey-auth must be mounted at /etc/valkey/auth (aclfile path)")

  cert = next(
    (d for d in yaml.safe_load_all(chart)
     if isinstance(d, dict) and d.get("kind") == "Certificate"
     and d["metadata"]["name"] == "narwhal-portal-valkey-tls"),
    None,
  )
  if cert is None or not cert["spec"].get("duration") or not cert["spec"].get("renewBefore"):
    raise AssertionError("Valkey Certificate needs explicit duration and renewBefore (no cert reload in 8.1)")

  match = re.findall(r'(?ms)^cat > "\$\{valkey_auth_dir\}/users\.acl" <<EOF\n(.*?)\nEOF$', bootstrap)
  if len(match) != 1:
    raise AssertionError("exactly one users.acl heredoc is required")
  lines = [l.strip() for l in match[0].splitlines() if l.strip() and not l.strip().startswith("#")]
  if len(lines) != 2 or lines[0] != "user default off":
    raise AssertionError(f"users.acl must be exactly 'user default off' + one portal user, got {lines}")
  tokens = lines[1].split()
  head = ["user", "portal", "on", ">${valkey_password}", "~*", "&live:events:chan"]
  if tokens[:6] != head:
    raise AssertionError(f"portal ACL line must start with {head}, got {tokens[:6]}")
  grants = tokens[6:]
  if any(not g.startswith("+") or g.startswith("+@") or g == "+all" for g in grants):
    raise AssertionError(f"portal ACL may only grant explicit +command entries, got {grants}")
  if len(grants) != len(set(grants)) or sorted(g[1:] for g in grants) != sorted(PORTAL_ACL_COMMANDS):
    raise AssertionError("portal ACL commands must equal the reviewed allowlist exactly")

  acl_writes = [
    l for l in bootstrap.splitlines()
    if not l.lstrip().startswith("#") and re.search(r">>?\s*\"?\S*users\.acl|\btee\b.*users\.acl", l)
  ]
  if len(acl_writes) != 1:
    raise AssertionError(f"users.acl must be written only by the reviewed heredoc, got {acl_writes}")
  if "-o name)" not in bootstrap or "get secret narwhal-portal-valkey-auth -n devtools \\\n  --ignore-not-found -o name" not in bootstrap:
    raise AssertionError("existence probe must be a separate `kubectl get secret --ignore-not-found -o name`")
  if "exists without a password; refusing to regenerate" not in bootstrap:
    raise AssertionError("an existing Secret without a usable password must abort (refusing to regenerate)")
  if "*AlreadyExists*)" not in bootstrap or "2>&1 >/dev/null)\"; then" not in bootstrap:
    raise AssertionError("first creation must handle AlreadyExists by re-reading the stored password")
  if len(re.findall(r"kubectl create secret generic narwhal-portal-valkey-auth", bootstrap)) != 2:
    raise AssertionError("atomic first create needs exactly one plain create plus one dry-run apply path")
  create_block = bootstrap.split("if ! valkey_create_err=", 1)[-1].split("; then", 1)[0]
  if "--dry-run" in create_block or "kubectl apply" in create_block:
    raise AssertionError("first creation must use atomic `kubectl create`, not apply/dry-run")
  if not re.search(r"openssl rand -hex 32 \| tr -d '\\n' > \"\$\{valkey_auth_dir\}/password\"", bootstrap):
    raise AssertionError("password must be generated newline-free: openssl rand -hex 32 | tr -d '\\n'")
  if re.search(r"openssl rand[^\n|]*>\s*\"?\$\{valkey_auth_dir\}/password", bootstrap):
    raise AssertionError("raw `openssl rand > password` writes a trailing newline (WRONGPASS)")
  if "[0-9a-f]{64}" not in bootstrap:
    raise AssertionError("bootstrap must validate the password shape (64 hex, no whitespace)")


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
  validate_structured(chart, bootstrap)
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
    "narwhal-portal-valkey-auth",
    '--from-file=password="${valkey_auth_dir}/password"',
    '--from-file=users.acl="${valkey_auth_dir}/users.acl"',
    "user default off",
    "user portal on >${valkey_password} ~* &live:events:chan",
    'VALKEY_URL="rediss://narwhal-portal-valkey.devtools.svc.cluster.local:6379"',
    'VALKEY_TLS="true"',
    "--owned-key VALKEY_PASSWORD",
    "--owned-key VALKEY_INSECURE_PRODUCTION",
    'stale_key="VALKEY_INSECURE_PRODUCTION"',
    "kubectl patch secret narwhal-portal-secrets -n devtools --type=json",
    "still present in narwhal-portal-secrets after removal",
  ]
  missing = [item for item in required_bootstrap if item not in bootstrap]
  if missing:
    raise AssertionError(f"bootstrap missing security contract fragments: {missing}")

  if 'os.environ["STALE_KEY"] in data' not in bootstrap or "jsonpath={.data.${stale_key}}" in bootstrap:
    raise AssertionError("stale-key probe must test key existence, not its value (empty value is still defined)")

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
    # Review-driven mutations (parsed checks): duplicate flag, commented-out default off,
    # privilege additions, a second user line, newline-producing generation, no cert duration.
    (chart.replace("            - --aclfile", '            - --port\n            - "6379"\n            - --aclfile', 1), bootstrap),
    (chart, bootstrap.replace("\nuser default off\nuser portal", "\n# user default off\nuser default on nopass\nuser portal", 1)),
    (chart, bootstrap.replace(" +unsubscribe\nEOF", " +unsubscribe +@all\nEOF", 1)),
    (chart, bootstrap.replace(" +unsubscribe\nEOF", " +unsubscribe +@dangerous\nEOF", 1)),
    (chart, bootstrap.replace(" +unsubscribe\nEOF", " +unsubscribe nopass\nEOF", 1)),
    (chart, bootstrap.replace(" +unsubscribe\nEOF", " +unsubscribe allcommands\nEOF", 1)),
    (chart, bootstrap.replace(" +unsubscribe\nEOF", " +unsubscribe\nuser portal on >x ~* +get\nEOF", 1)),
    (chart, bootstrap.replace("openssl rand -hex 32 | tr -d '\\n' >", "openssl rand -hex 32 >", 1)),
    (chart, bootstrap.replace("[0-9a-f]{64}", "[0-9a-f]+")),
    (chart.replace("  renewBefore: 720h\n", "", 1), bootstrap),
  ]
  for index, (mutant_chart, mutant_bootstrap) in enumerate(mutants, start=1):
    if (mutant_chart, mutant_bootstrap) == (chart, bootstrap):
      raise AssertionError(f"mutation {index} did not change the input (stale anchor)")
    try:
      validate(mutant_chart, mutant_bootstrap)
    except AssertionError:
      continue
    raise AssertionError(f"security regression mutation {index} was accepted")

  # Round-2 mutations assert the rejection REASON, not only that something failed.
  vk_mounts = "          volumeMounts:\n            - name: valkey-tls"
  extra = "            - /etc/valkey/auth/users.acl\n"
  reasoned = [
    (chart.replace('            - --port\n            - "0"\n', '            - --tls-session-caching\n            - "0"\n', 1).replace(vk_mounts, '          args:\n            - --port\n            - "6380"\n' + vk_mounts, 1), bootstrap, "must be exactly ['0']"),
    (chart.replace(extra, extra + '            - --port\n          args:\n            - "6380"\n', 1), bootstrap, "exactly once"),
    (chart.replace(extra, extra + "            - --include\n            - /tmp/extra.conf\n", 1), bootstrap, "bypass the ACL"),
    (chart.replace(extra, extra + "            - --acl-pubsub-default\n            - allchannels\n", 1), bootstrap, "bypass the ACL"),
    (chart.replace(extra, extra + "            - --enable-debug-command\n            - \"yes\"\n", 1), bootstrap, "allowlist"),
    (chart.replace(vk_mounts, '          env:\n            - name: X\n              value: y\n' + vk_mounts, 1), bootstrap, "env entries"),
    (
      chart.replace(
        "      volumes:\n        - name: valkey-tls",
        '        - name: valkey-plain\n          image: docker.io/valkey/valkey:8-alpine\n          command: [valkey-server, --port, "6380"]\n      volumes:\n        - name: valkey-tls',
        1,
      ),
      bootstrap, "launcher",
    ),
    (chart.replace("                path: users.acl\n", "                path: users2.acl\n", 1), bootstrap, "path users.acl"),
    (chart, bootstrap.replace("EOF\n}\napply_valkey_auth", "EOF\necho 'user default on nopass' >> \"${valkey_auth_dir}/users.acl\"\n}\napply_valkey_auth", 1), "reviewed heredoc"),
    (chart, bootstrap.replace("*AlreadyExists*)", "*NeverMatches*)", 1), "AlreadyExists"),
    (chart, bootstrap.replace("exists without a password; refusing to regenerate", "exists", 1), "refusing to regenerate"),
    (chart, bootstrap.replace("kubectl patch secret narwhal-portal-secrets -n devtools --type=json", "kubectl get secret narwhal-portal-secrets -n devtools -o name", 1), "missing security contract fragments"),
    # Empty-value case: a value-based (jsonpath) probe never sees a key present with "".
    (chart, bootstrap.replace('raise SystemExit(0 if os.environ["STALE_KEY"] in data else 1)', 'raise SystemExit(0 if data.get(os.environ["STALE_KEY"]) else 1)', 1), "existence"),
    (chart, bootstrap.replace("still present in narwhal-portal-secrets after removal", "removed", 1), "missing security contract fragments"),
    (chart, bootstrap.replace("  --ignore-not-found -o name)", "  --ignore-not-found -o jsonpath='{.data.password}')", 1), "existence probe"),
    (chart, bootstrap.replace('users.acl" 2>&1 >/dev/null)"; then', 'users.acl" --dry-run=client -o yaml 2>&1 >/dev/null)"; then', 1), "atomic"),
  ]
  for index, (mutant_chart, mutant_bootstrap, reason) in enumerate(reasoned, start=1):
    if (mutant_chart, mutant_bootstrap) == (chart, bootstrap):
      raise AssertionError(f"reasoned mutation {index} did not change the input (stale anchor)")
    try:
      validate(mutant_chart, mutant_bootstrap)
    except AssertionError as exc:
      if reason in str(exc):
        continue
      raise AssertionError(f"reasoned mutation {index} rejected for the wrong reason: {exc}") from exc
    raise AssertionError(f"security regression reasoned mutation {index} was accepted")

  print("PASS: Valkey TLS, ACL, secret wiring, ingress scope, and {} negative mutations".format(len(mutants) + len(reasoned)))


if __name__ == "__main__":
  main()
