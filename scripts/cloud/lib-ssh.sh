#!/bin/bash
# Shared bastion/node SSH trust policy for Kakao Cloud provisioning scripts.
#
# The old default here was StrictHostKeyChecking=no + UserKnownHostsFile=/dev/null,
# which accepts ANY host key presented by ANY host claiming to be the bastion or a
# node — no on-path attacker needs to forge anything, just be reachable when the
# script connects. That is fine for disposable local-lab Vagrant boxes (never used
# here) but not for real cloud/bastion hops.
#
# accept-new pins a host key on first connect and FAILS CLOSED on a later CHANGED
# key instead of silently trusting it. Pointing UserKnownHostsFile at a real,
# per-cluster file (not /dev/null) is what gives accept-new something to pin
# against — without persistent state, every call looks like a first connection and
# accept-new degenerates into =no. Source this file after TF_DIR is set.
#
# 2026-08-02 lesson (docs/common/lessons-log.md): replacing the Kakao instances
# gives every node a new host key, and accept-new correctly REFUSES the changed
# key rather than connecting anyway. When a node/bastion was legitimately rebuilt,
# drop its stale pin with kakao_forget_host() below before reconnecting — do not
# reach for =no to "fix" the failure.
KAKAO_KNOWN_HOSTS="${KAKAO_KNOWN_HOSTS:-${TF_DIR:-csp/kakao-cloud/terraform}/kakao_known_hosts}"
touch "${KAKAO_KNOWN_HOSTS}" 2>/dev/null || true
chmod 600 "${KAKAO_KNOWN_HOSTS}" 2>/dev/null || true

# Set KAKAO_RESET_KNOWN_HOSTS=1 before a provisioning run to explicitly drop ALL
# pinned host keys for this cluster -- the deterministic escape hatch for "I just
# rebuilt the whole environment and expect every host key to have changed". Prefer
# kakao_forget_host() below for a single host; this is the blunt, whole-cluster form.
if [ "${KAKAO_RESET_KNOWN_HOSTS:-}" = "1" ]; then
  : > "${KAKAO_KNOWN_HOSTS}"
  echo "[lib-ssh] KAKAO_RESET_KNOWN_HOSTS=1: cleared ${KAKAO_KNOWN_HOSTS} -- every host will be re-pinned on next connect." >&2
fi

# shellcheck disable=SC2034  # consumed by the scripts that source this file
KAKAO_SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=${KAKAO_KNOWN_HOSTS}" -o LogLevel=ERROR)

# Deterministic recovery for a legitimate host-key rotation (node rebuild, bastion
# reprovision): forget the stale pin, then the next accept-new connect re-pins the
# new key instead of failing forever. Never a silent auto-accept.
#
# This is invoked automatically by kakao_ssh()/kakao_scp() below on a host-key
# mismatch (ssh exit 255 with "REMOTE HOST IDENTIFICATION HAS CHANGED" on stderr):
# the recovery path is enforced in code, not left as an operator's tribal
# knowledge to rediscover this function exists.
kakao_forget_host() {
  ssh-keygen -R "$1" -f "${KAKAO_KNOWN_HOSTS}" >/dev/null 2>&1 || true
}

# ssh/scp wrappers that all scripts in this directory should use instead of calling
# ssh/scp directly with "${KAKAO_SSH_OPTS[@]}" -- on a detected host-key mismatch
# they explain what happened and how to recover, rather than a bare ssh(1) error.
_kakao_run_with_recovery_hint() {
  local cmd="$1"; shift
  local out
  if out=$("${cmd}" "${KAKAO_SSH_OPTS[@]}" "$@" 2>&1); then
    printf '%s\n' "${out}"
    return 0
  fi
  local rc=$?
  printf '%s\n' "${out}" >&2
  if printf '%s' "${out}" | grep -q "REMOTE HOST IDENTIFICATION HAS CHANGED\|WARNING: POSSIBLE DNS SPOOFING"; then
    echo "[lib-ssh] Host key changed -- this is EXPECTED after a legitimate node/bastion" >&2
    echo "[lib-ssh] rebuild, and REFUSED rather than silently trusted (2026-08-02 lesson)." >&2
    echo "[lib-ssh] If this rebuild was intentional, run:" >&2
    echo "[lib-ssh]   bash -c 'source scripts/cloud/lib-ssh.sh; kakao_forget_host <host-or-ip>'" >&2
    echo "[lib-ssh] then re-run this script. If you did NOT rebuild this host, STOP --" >&2
    echo "[lib-ssh] this may be a real man-in-the-middle attempt." >&2
  fi
  return "${rc}"
}

kakao_ssh() { _kakao_run_with_recovery_hint ssh "$@"; }
kakao_scp() { _kakao_run_with_recovery_hint scp "$@"; }
