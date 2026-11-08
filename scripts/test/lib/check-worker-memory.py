#!/usr/bin/env python3
"""R323: worker VM memory is 8GB everywhere it is set or documented (D-WORKER-MEMORY, 2026-11-08).

Vagrantfile WORKER_MEMORY=8192 (MASTER_MEMORY stays 6144); README/README-ko config line, VM table
and RAM requirement, architecture.md diagram and table, operations.md/troubleshooting.md, the
SessionStart hook and agent text carry 8GB; no live doc still says "worker 6GB"; the apply
procedure (cordon/drain, halt+up --no-provision, uncordon, CNPG required anti-affinity) exists.
--mutation-verify re-runs the check against broken copies.
"""
import re
import sys

FILES = {
    "vf": "Vagrantfile",
    "readme": "README.md",
    "readme_ko": "README-ko.md",
    "arch": "docs/common/architecture.md",
    "ops": "docs/vagrant/operations.md",
    "trouble": "docs/common/troubleshooting.md",
    "hook": ".claude/hooks/session-start.sh",
    "scout": ".claude/agents/infra-scout.md",
    "localdev": "portal/docs/local-dev.md",
}
STALE = re.compile(r"worker[^\n|]{0,24}\b6 ?(GB|GiB)\b|\b6 ?(GB|GiB) ?worker", re.I)


def check(t):
    e = []
    m = re.search(r"^WORKER_MEMORY = (\d+)\b", t["vf"], re.M)
    if not m or m.group(1) != "8192":
        e.append(f"Vagrantfile WORKER_MEMORY must be 8192 (got {m and m.group(1)})")
    m = re.search(r"^MASTER_MEMORY = (\d+)\b", t["vf"], re.M)
    if not m or m.group(1) != "6144":
        e.append("Vagrantfile MASTER_MEMORY must stay 6144")
    for k in ("readme", "readme_ko"):
        if not re.search(r"^WORKER_MEMORY = 8192\b", t[k], re.M):
            e.append(f"{FILES[k]}: config snippet must show WORKER_MEMORY = 8192")
        if "2 CPU, 8GB      │" not in t[k]:
            e.append(f"{FILES[k]}: VM table must show the worker column as 8GB")
        if "3 workers x 8GB = 42GB" not in t[k] and "워커 3대 x 8GB = 42GB" not in t[k]:
            e.append(f"{FILES[k]}: RAM requirement must be 3 masters x 6GB + 3 workers x 8GB = 42GB")
    if t["arch"].count("2 CPU / 8GB RAM") != 3 or len(re.findall(r"\| narwhal-worker-\d \|[^\n]*\| 8 GiB \|", t["arch"])) != 3:
        e.append("architecture.md: diagram and table must show 8GB for all three workers")
    if "Worker Memory: 8GB" not in t["ops"]:
        e.append("operations.md: resource allocation must say Worker Memory: 8GB")
    if "Worker 8GB (platform apps)" not in t["hook"]:
        e.append("session-start.sh: context must say Worker 8GB")
    for k in ("ops", "trouble", "hook", "scout", "localdev", "arch", "readme", "readme_ko"):
        for line in t[k].splitlines():
            if STALE.search(line) and "2026-11-08" not in line and "이전" not in line:
                e.append(f"{FILES[k]}: stale worker 6GB wording: {line.strip()[:90]}")
    sec = re.search(r"^## 13\. Worker 메모리 8GB 적용.*?(?=^## 관련 문서)", t["ops"], re.S | re.M)
    if not sec:
        e.append("operations.md: section '13. Worker 메모리 8GB 적용' missing")
    else:
        for need in ("cordon", "drain", "vagrant halt <worker> && vagrant up <worker> --no-provision",
                     "uncordon", "podAntiAffinityType=required", "#590", "allocatable"):
            if need not in sec.group(0):
                e.append(f"operations.md section 13 must mention {need!r}")
    return e


def load():
    return {k: open(p, encoding="utf-8").read() for k, p in FILES.items()}


MUTATIONS = {
    "Vagrantfile back to 6144": ("vf", "WORKER_MEMORY = 8192", "WORKER_MEMORY = 6144"),
    "master raised too": ("vf", "MASTER_MEMORY = 6144", "MASTER_MEMORY = 8192"),
    "README table 6GB": ("readme", "2 CPU, 8GB      │", "2 CPU, 6GB      │"),
    "README-ko RAM total stale": ("readme_ko", "워커 3대 x 8GB = 42GB", "워커 3대 x 6GB = 36GB"),
    "architecture table 6 GiB": ("arch", "| narwhal-worker-2 | 192.168.56.22 | worker | 2 | 8 GiB |",
                                 "| narwhal-worker-2 | 192.168.56.22 | worker | 2 | 6 GiB |"),
    "hook says Worker 6GB": ("hook", "Worker 8GB (platform apps)", "Worker 6GB (platform apps)"),
    "troubleshooting says 6GB": ("trouble", "Worker 메모리 8GB 미만이면", "Worker 메모리 6GB 미만이면"),
    "no-provision dropped": ("ops", "--no-provision`. `--no-provision", "`. `"),
    "CNPG caveat dropped": ("ops", "podAntiAffinityType=required", "podAntiAffinityType=preferred"),
}


def main():
    t = load()
    errors = check(t)
    if errors:
        print("\n".join("FAIL: " + x for x in errors))
        return 1
    print("worker memory 8GB ok")
    if "--mutation-verify" in sys.argv:
        for name, (k, old, new) in MUTATIONS.items():
            if old not in t[k]:
                print(f"FAIL: mutation anchor missing: {name}")
                return 1
            m = dict(t)
            m[k] = t[k].replace(old, new)
            if not check(m):
                print(f"FAIL: mutation not rejected: {name}")
                return 1
        print(f"PASS: {len(MUTATIONS)} mutations rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
