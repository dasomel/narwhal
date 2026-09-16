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

# shellcheck disable=SC2034  # consumed by the scripts that source this file
KAKAO_SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=${KAKAO_KNOWN_HOSTS}" -o LogLevel=ERROR)

# Deterministic recovery for a legitimate host-key rotation (node rebuild, bastion
# reprovision): forget the stale pin, then the next accept-new connect re-pins the
# new key instead of failing forever. Never a silent auto-accept.
kakao_forget_host() {
  ssh-keygen -R "$1" -f "${KAKAO_KNOWN_HOSTS}" >/dev/null 2>&1 || true
}
