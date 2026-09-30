#!/usr/bin/env python3
"""Check kubeadm's repository-declared kubelet and apiserver security baseline."""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # The text fallback below still rejects unparsed declarations.
    yaml = None

ROOT = Path(__file__).resolve().parents[3]
INIT = ROOT / "scripts/cluster/02-init-cluster.sh"


def configuration_blocks(text, kind):
    blocks = re.findall(
        r"(?ms)^kind:\s*" + re.escape(kind) + r"\s*$.*?(?=^kind:|\Z)", text
    )
    return blocks


def parse_kubeadm_documents(text):
    match = re.search(
        r"(?ms)^cat <<EOF > /tmp/kubeadm-config\.yaml\s*\n(.*?)^EOF\s*$", text
    )
    if not match:
        raise ValueError("unresolvable kubeadm source: config heredoc is missing")
    documents = None
    if yaml is not None:
        try:
            # CERT_SANS is a shell-generated YAML sequence, so replace that one
            # template splice with a valid placeholder before parsing documents.
            parse_text = re.sub(r"(?m)^(\s*certSANs):\$\{CERT_SANS\}\s*$",
                                r"\1: []", match.group(1))
            documents = list(yaml.safe_load_all(parse_text))
        except yaml.YAMLError:
            # Shell substitutions can make otherwise valid YAML unparseable.
            documents = None
    if documents is not None:
        kubelet = [d for d in documents if isinstance(d, dict)
                   and d.get("kind") == "KubeletConfiguration"]
        apiserver = [d for d in documents if isinstance(d, dict)
                     and d.get("kind") == "ClusterConfiguration"]
        if len(kubelet) == 1 and len(apiserver) == 1:
            return kubelet[0], apiserver[0]
    kubelet = configuration_blocks(match.group(1), "KubeletConfiguration")
    apiserver = configuration_blocks(match.group(1), "ClusterConfiguration")
    if len(kubelet) != 1 or len(apiserver) != 1:
        raise ValueError("unresolvable kubeadm source: embedded documents cannot be parsed")
    return kubelet[0], apiserver[0]


def apiserver_args(apiserver):
    names = ("authorization-mode", "enable-admission-plugins")
    if isinstance(apiserver, dict):
        args = (apiserver.get("apiServer") or {}).get("extraArgs") or {}
        if isinstance(args, dict):
            return {name: str(args[name]) for name in names if name in args}
        if isinstance(args, list):
            parsed = {}
            for item in args:
                if isinstance(item, dict) and item.get("name") in names:
                    if "value" not in item:
                        raise ValueError(
                            f"unresolvable apiserver extraArgs declaration: {item['name']}"
                        )
                    parsed[str(item["name"])] = str(item["value"])
                elif not isinstance(item, dict):
                    raise ValueError("unresolvable apiserver extraArgs list item")
            return parsed
        if args:
            raise ValueError("unresolvable apiserver extraArgs declaration")
        return {}

    # Template fallback: compare every active target declaration with those parsed.
    active = [line for line in apiserver.splitlines()
              if not line.lstrip().startswith(("#", "//"))]
    declarations = sum(
        len(re.findall(r"(?m)^\s*(?:-\s*name:\s*|)" + re.escape(name) + r"\s*:",
                       "\n".join(active)))
        for name in names
    )
    parsed = {}
    for name, value in re.findall(
        r"(?ms)^\s*-\s*name:\s*(authorization-mode|enable-admission-plugins)\s*\n"
        r"\s+value:\s*([^\n]*)$", apiserver
    ):
        parsed[name] = value.strip().strip("\"'").strip()
    map_values = re.findall(
        r"(?m)^\s*(authorization-mode|enable-admission-plugins)\s*:\s*([^\n#]+)",
        "\n".join(active),
    )
    for name, value in map_values:
        parsed[name] = value.strip().strip("\"'").strip()
    if declarations != len(parsed):
        raise ValueError("unresolvable apiserver extraArgs declaration")
    return parsed


def checks(kubelet, apiserver):
    if isinstance(kubelet, dict):
        kubelet = yaml.safe_dump(kubelet) if yaml is not None else str(kubelet)
    authz = re.search(r"(?m)^\s*mode:\s*([^\s#]+)", kubelet)
    anonymous = re.search(r"(?ms)^authentication:\s*\n(?:[ \t]+.*\n)*?"
                          r"[ \t]+anonymous:\s*\n[ \t]+enabled:\s*(true|false)\b", kubelet)
    read_only = re.search(r"(?m)^readOnlyPort:\s*(\d+)\s*$", kubelet)
    args = apiserver_args(apiserver)
    authorization = args.get("authorization-mode", "Node,RBAC")
    plugins = args.get("enable-admission-plugins", "NodeRestriction")
    return {
        "kubelet authorization is Webhook": authz is not None and authz.group(1) == "Webhook",
        "kubelet anonymous authentication is disabled": anonymous is not None and anonymous.group(1) == "false",
        "apiserver Node authorizer is enabled": "Node" in authorization.split(","),
        "apiserver NodeRestriction admission plugin is enabled": "NodeRestriction" in plugins.split(","),
        "kubelet readOnlyPort is disabled": read_only is not None and read_only.group(1) == "0",
    }


def repository_sources(init_text=None):
    if not INIT.is_file():
        raise ValueError(f"unresolvable kubeadm source: {INIT.relative_to(ROOT)} is missing")
    init_text = init_text if init_text is not None else INIT.read_text(encoding="utf-8")
    if not re.search(r"kubeadm init\s+--config=/tmp/kubeadm-config\.yaml", init_text):
        raise ValueError("unresolvable kubeadm source: init does not consume its declared config")
    kubelet, apiserver = parse_kubeadm_documents(init_text)

    # Any additional active apiserver flag declaration can override kubeadm defaults.
    # Check tracked bootstrap/config sources, while excluding checks and documentation.
    override_files = []
    for base in (ROOT / "scripts", ROOT / "csp", ROOT / "gitops"):
        for path in base.rglob("*"):
            if (not path.is_file() or path == INIT or "/test/" in path.as_posix()
                    or "/verify/" in path.as_posix()):
                continue
            if path.suffix not in {".sh", ".yaml", ".yml", ".conf"}:
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            active = "\n".join(line for line in text.splitlines()
                                 if not line.lstrip().startswith(("#", "//")))
            if re.search(
                r"(?:authorization-mode|enable-admission-plugins|readOnlyPort|"
                r"anonymous-auth|^\s*anonymous:)", active
            ):
                override_files.append(path)
    # The init config is authoritative; other occurrences are only acceptable when
    # they do not alter the kubeadm-generated apiserver arguments.
    unexpected = [p for p in override_files if p != INIT]
    if unexpected:
        names = ", ".join(str(p.relative_to(ROOT)) for p in unexpected)
        raise ValueError(f"unresolvable apiserver overrides require review: {names}")
    return kubelet, apiserver, 1 + len(override_files)


def mutation_verify(init_text):
    kubelet, apiserver = parse_kubeadm_documents(init_text)
    cases = [
        ("kubelet authorization is Webhook", init_text.replace("mode: Webhook", "mode: AlwaysAllow", 1)),
        ("kubelet anonymous authentication is disabled", init_text.replace("enabled: false", "enabled: true", 1)),
        ("apiserver Node authorizer is enabled", init_text.replace('value: "Node,RBAC"', 'value: "RBAC"', 1)),
        ("apiserver NodeRestriction admission plugin is enabled", init_text.replace('value: "NodeRestriction"', 'value: ""', 1)),
        ("kubelet readOnlyPort is disabled", init_text.replace("readOnlyPort: 0", "readOnlyPort: 10255", 1)),
    ]
    parsed_docs = list(yaml.safe_load_all(re.sub(
        r"(?m)^(\s*certSANs):\$\{CERT_SANS\}\s*$", r"\1: []",
        re.search(r"(?ms)^cat <<EOF > /tmp/kubeadm-config\.yaml\s*\n(.*?)^EOF\s*$",
                  init_text).group(1))))
    cluster = next(doc for doc in parsed_docs if doc.get("kind") == "ClusterConfiguration")
    old_args = cluster["apiServer"]["extraArgs"]
    map_args = {item["name"]: item["value"] for item in old_args}
    for name, value, label in (
        ("authorization-mode", "RBAC", "apiserver Node authorizer is enabled (map form)"),
        ("enable-admission-plugins", "", "apiserver NodeRestriction admission plugin is enabled (map form)"),
    ):
        mutated_docs = [dict(doc) if doc.get("kind") == "ClusterConfiguration" else doc
                        for doc in parsed_docs]
        mutated_cluster = next(doc for doc in mutated_docs
                               if doc.get("kind") == "ClusterConfiguration")
        mutated_args = dict(map_args)
        mutated_args[name] = value
        mutated_cluster["apiServer"] = dict(cluster["apiServer"])
        mutated_cluster["apiServer"]["extraArgs"] = mutated_args
        serialized = yaml.safe_dump_all(mutated_docs, explicit_start=True, sort_keys=False)
        mutated_text = re.sub(
            r"(?ms)(^cat <<EOF > /tmp/kubeadm-config\.yaml\s*\n).*?^EOF\s*$",
            lambda match: match.group(1) + serialized.rstrip() + "\nEOF",
            init_text, count=1,
        )
        cases.append((label, mutated_text))
    for name, mutated_text in cases:
        if mutated_text == init_text:
            raise ValueError(f"mutation could not find source declaration: {name}")
        try:
            mutated_kubelet, mutated_apiserver, _ = repository_sources(mutated_text)
            passed = checks(mutated_kubelet, mutated_apiserver)[name.split(" (map form)")[0]]
        except ValueError:
            passed = False
        if passed:
            raise ValueError(f"mutation survived: {name}")
    print(f"PASS --mutation-verify rejected all {len(cases)} baseline mutations through repository_sources")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    try:
        init_text = INIT.read_text(encoding="utf-8")
        kubelet, apiserver, source_count = repository_sources(init_text)
        results = checks(kubelet, apiserver)
        failures = [name for name, passed in results.items() if not passed]
        print(f"Evaluated {len(results)} assertions across {source_count} configuration source(s)")
        for name, passed in results.items():
            print(f"{'PASS' if passed else 'FAIL'} {name}")
        if args.mutation_verify:
            mutation_verify(init_text)
        print("WARN runtime verification remains for effective node config, auth behavior, "
              "kubelet network exposure, and the API-server-to-kubelet path")
        if failures:
            raise ValueError("baseline violations: " + "; ".join(failures))
    except (OSError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
