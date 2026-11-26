#!/usr/bin/env python3
"""R413: guards for an NFS vmdk that outlives master-1 (2026-11-26 live bring-up).

D-NFS-STALE-LOCK: the before-up trigger removes <vmdk>.lck only when vmrun reports no running VM
whose vmx names the vmdk (an unreadable vmx counts as a holder).
D-NFS-ORPHAN-DISK: a not-created master-1 with a written absolute-path vmdk aborts in the trigger
and in up.sh (same size bound) unless NARWHAL_NFS_REUSE_DISK says so explicitly.
D-OPENBAO-ORPHAN-STORAGE: 08-4 fails (no longer WARNs) on an initialized, sealed OpenBao whose
key is missing, probing Secret existence separately from API errors.

Static text contract; --mutation-verify drops each fragment and requires the check to fail.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
FILES = {
  "vagrantfile": ROOT / "Vagrantfile",
  "up": ROOT / "scripts/up.sh",
  "storage": ROOT / "scripts/cluster/08-4-storage.sh",
}
REUSE = "yes-reuse-previous-cluster-data"

# (file key, fragment, what it guarantees)
REQUIRED = [
  ("vagrantfile", 'lock = "#{target}.lck"', "stale lock path is the vmdk's own .lck"),
  ("vagrantfile", "IO.popen([vmrun, \"list\"]", "running VMs come from vmrun list"),
  ("vagrantfile", "File.read(vmx).include?(NFS_VMDK_NAME)", "a VM holds the lock when its vmx names the vmdk"),
  ("vagrantfile", "rescue StandardError\n                    true", "an unreadable vmx counts as a holder"),
  ("vagrantfile", "if holder\n", "a held lock is left alone"),
  ("vagrantfile", "if File.directory?(lock) && File.executable?(vmrun)", "no vmrun -> nothing removed"),
  ("vagrantfile", "File.binread(target, 65_536)[/^RW (\\d+) /, 1]", "capacity read from the vmdk descriptor"),
  ("vagrantfile", "cap_gb * 256 * 1024 + 64 * 1024 * 1024", "written-disk size bound uses that capacity"),
  ("vagrantfile", "            end\n            next if File.exist?(target)\n", "existing vmdk never reaches vdiskmanager (R327 anchor)"),
  ("vagrantfile", "machine.state.id == :not_created && !NFS_VMDK_IN_VMDIR && written", "orphan = not created + written abs vmdk"),
  ("vagrantfile", f'ENV["NARWHAL_NFS_REUSE_DISK"] != "{REUSE}"', "explicit reuse escape hatch"),
  ("vagrantfile", 'abort "REFUSING: master-1 is not created but', "orphan disk aborts"),
  ("up", 'nfs_vmdk=".vagrant/narwhal-nfs/narwhal-nfs.vmdk"', "up.sh checks the same vmdk"),
  ("up", '[ "${nfs_m1_state}" = "not" ] || [ "${nfs_m1_state}" = "not_created" ]', "up.sh orphan = master-1 not created (vm_state prints \"not\")"),
  ("up", f'"${{NARWHAL_NFS_REUSE_DISK:-}}" != "{REUSE}"', "up.sh shares the escape hatch"),
  ("up", "$((nfs_cap_gb * 256 * 1024 + 64 * 1024 * 1024))", "up.sh uses the same size bound"),
  ("up", "LC_ALL=C grep -a -o -E '^RW [0-9]+'", "up.sh reads capacity from the descriptor"),
  ("up", 'nfs_size=$(wc -c < "${nfs_vmdk}"', "portable size (no GNU/BSD stat split)"),
  ("storage", "if key_read=$(kubectl get secret openbao-init -n storage -o jsonpath='{.data.unseal_keys_b64}' 2>\"${key_err_file}\"); then", "one key read, stderr kept apart from the value"),
  ("up", "''|*[!0-9]*) echo \"ERROR: NARWHAL_NFS_DISK_GB must be an integer >= 0", "up.sh rejects a non-integer disk size by name"),
  ("storage", '*) echo "ERROR: cannot read Secret storage/openbao-init: ${key_err}" >&2; exit 1 ;;', "API error exits as such, never as missing key"),
  ("storage", "*NotFound*)", "absent Secret is told apart from API errors"),
  ("storage", "is initialized and sealed, and storage/openbao-init has no unseal_keys_b64", "present-without-key is reported"),
]
FORBIDDEN = [
  ("storage", "-o jsonpath='{.data.unseal_keys_b64}' 2>&1", "key read must not merge stderr into the value"),
  ("storage", 'echo "WARN: OpenBao initialized but unseal key not found in openbao-init secret"', "missing key must not be WARN-only"),
]


def violations(texts: dict) -> list:
  out = [f"{k}: missing {why}" for k, frag, why in REQUIRED if frag not in texts[k]]
  out += [f"{k}: {why}" for k, frag, why in FORBIDDEN if frag in texts[k]]
  # The 08-4 missing-key branch must exit non-zero: the exit 1 follows the explanation block.
  storage = texts["storage"]
  start = storage.find("D-OPENBAO-ORPHAN-STORAGE")
  end = storage.find("re-run Phase 2.", start)
  if start < 0 or end < 0 or "exit 1" not in storage[end:end + 80]:
    out.append("storage: missing-key branch does not exit 1")
  # Vagrantfile and up.sh share one reuse value (no second spelling).
  if texts["vagrantfile"].count(REUSE) < 2 or texts["up"].count(REUSE) < 2:
    out.append("reuse escape hatch is not documented in the abort messages")
  return out


def main() -> int:
  texts = {k: p.read_text() for k, p in FILES.items()}
  found = violations(texts)
  if found:
    print("FAIL: " + "; ".join(found))
    return 1
  if "--mutation-verify" in sys.argv:
    caught = 0
    mutations = [(k, frag, "") for k, frag, _ in REQUIRED]
    mutations.append(("storage", "re-run Phase 2.\" >&2\n      exit 1", "re-run Phase 2.\" >&2\n      true"))
    mutations.append(("storage", "      exit 1\n    fi\n  else\n    echo \"OpenBao already unsealed",
                      "      echo \"WARN: OpenBao initialized but unseal key not found in openbao-init secret\"\n    fi\n  else\n    echo \"OpenBao already unsealed"))
    for key, frag, repl in mutations:
      if frag not in texts[key]:
        print(f"FAIL: mutation anchor not found in {key}: {frag!r}")
        return 1
      mutated = dict(texts)
      mutated[key] = texts[key].replace(frag, repl, 1)
      if not violations(mutated):
        print(f"FAIL: mutation survived in {key}: {frag!r}")
        return 1
      caught += 1
    print(f"PASS: NFS disk reuse guards; {caught} mutations caught")
  else:
    print("PASS: NFS disk reuse guards")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
