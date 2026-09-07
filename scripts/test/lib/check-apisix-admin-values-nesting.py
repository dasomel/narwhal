#!/usr/bin/env python3
"""Assert the APISIX bootstrap values heredoc nests the Admin API allowlist under
`apisix.admin.allow.ipList` — chart v2.13.0 has no top-level `admin:` key, so Helm
silently drops one and the allowlist falls back to the chart's 127.0.0.1/24 default."""
import re
import sys

import yaml

DEFAULT_PATH = "scripts/cluster/08-1-networking.sh"
HEREDOC_START = re.compile(r"^cat > /tmp/apisix-values\.yaml << 'EOF'\s*$")


def extract_heredoc(path: str) -> str:
    lines = []
    in_block = False
    with open(path) as f:
        for line in f:
            if not in_block:
                if HEREDOC_START.match(line):
                    in_block = True
                continue
            if line.rstrip("\n") == "EOF":
                break
            lines.append(line)
    return "".join(lines)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    text = extract_heredoc(path)
    problems = []
    if not text.strip():
        problems.append("apisix-values heredoc not found (markers changed?)")
        for problem in problems:
            print(f"{path}: {problem}", file=sys.stderr)
        return 1

    # The heredoc is unquoted-'EOF' (no shell interpolation), but it embeds a
    # ${POD_NETWORK_CIDR} shell variable inside a YAML string — substitute a
    # placeholder so it parses as YAML.
    yaml_text = text.replace("${POD_NETWORK_CIDR}", "10.244.0.0/16")
    try:
        values = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        problems.append(f"heredoc is not valid YAML: {exc}")
        values = None

    if values is not None:
        if "admin" in values:
            problems.append(
                "top-level `admin:` key present — the chart has no such key and Helm "
                "silently drops it; nest under `apisix.admin` instead"
            )
        ip_list = (
            values.get("apisix", {})
            .get("admin", {})
            .get("allow", {})
            .get("ipList")
        )
        if not ip_list:
            problems.append("apisix.admin.allow.ipList is missing or empty")

    for problem in problems:
        print(f"{path}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
