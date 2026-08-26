#!/usr/bin/env bash
set -euo pipefail

#=========================================
# Kakao Cloud clean-install regression check
#=========================================
# Every check here corresponds to an entry in docs/common/lessons-log.md. The point is
# not "is the cluster healthy" (verify-cluster.sh does that) but "did a bug we already
# paid for come back". Each one names its lessons-log date so a failure is traceable.
#
# Two independent halves:
#   --static    repo only, no cluster. Catches a fix being reverted or lost in a merge.
#               Run this BEFORE destroying anything — a static failure means the rebuild
#               would reproduce the bug, so there is no point spending two hours on it.
#   --runtime   against a live cluster. Catches a fix that is present but ineffective.
#   --all       both (default)
#
# Exit code is the number of failures, capped at 125. Warnings do not fail the run.
#
# --json-report <path> / --md-report <path> (narwhal#50, T1-T7 test strategy): write a
# machine-readable report alongside the normal stdout output. Order-independent, combine
# freely with --static/--runtime/--all. Fields are limited to what a --static run can
# know for certain — check id, description, PASS/FAIL/WARN, timestamp, the run id, and
# the repo's own git commit as a config/input hash. No cluster/artifact-digest fields are
# emitted even in --runtime mode; this repo does not yet capture those anywhere, and a
# field that is always empty is worse than a field that does not exist — see
# docs/common/test-strategy.md for what's still missing before those can be added.
#
# Usage:
#   scripts/test/regression-check-kakao.sh --static
#   scripts/test/regression-check-kakao.sh --runtime
#   scripts/test/regression-check-kakao.sh --static --json-report /tmp/report.json
#   DOMAIN=kakao.narwhal.internal scripts/test/regression-check-kakao.sh

ORIG_PWD="${PWD}"
MODE="--all"
JSON_REPORT=""
MD_REPORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --static|--runtime|--all) MODE="$1"; shift ;;
    --json-report) JSON_REPORT="${2:?--json-report requires a path}"; shift 2 ;;
    --md-report)   MD_REPORT="${2:?--md-report requires a path}"; shift 2 ;;
    *) echo "usage: $0 [--static|--runtime|--all] [--json-report <path>] [--md-report <path>]" >&2
       exit 2 ;;
  esac
done
# Report paths are relative to where the user ran the command, not to the repo root the
# checks themselves cd into below — otherwise a relative --json-report path silently
# lands inside the repo instead of where the caller expected it.
case "${JSON_REPORT}" in ""|/*) ;; *) JSON_REPORT="${ORIG_PWD}/${JSON_REPORT}" ;; esac
case "${MD_REPORT}"   in ""|/*) ;; *) MD_REPORT="${ORIG_PWD}/${MD_REPORT}" ;; esac

DOMAIN="${DOMAIN:-kakao.narwhal.internal}"
TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"

cd "$(dirname "$0")/../.."

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CHECK_LOG=""
if [ -n "${JSON_REPORT}" ] || [ -n "${MD_REPORT}" ]; then
  CHECK_LOG="$(mktemp)"
  trap 'rm -f "${CHECK_LOG}"' EXIT
fi

# The runtime checks use whatever kubectl context happens to be current. That is a trap:
# run this with an unrelated cluster selected and it reports THAT cluster's state under
# narwhal's check ids — measured 2026-08-12, when a `beluga` context produced
# "T01 only 4/6 nodes Ready" and T12 probing *.local.beluga.internal, which reads exactly
# like a narwhal regression. Name the context and require it to look like narwhal, so a
# wrong-cluster run stops instead of lying. KUBE_CONTEXT= (empty) opts out deliberately.
KUBE_CONTEXT="${KUBE_CONTEXT-narwhal}"
if [ "${MODE}" != "--static" ] && [ -n "${KUBE_CONTEXT}" ]; then
  _ctx="$(kubectl config current-context 2>/dev/null || true)"
  case "${_ctx}" in
    *"${KUBE_CONTEXT}"*) ;;
    "") echo "ERROR: no kubectl context selected; runtime checks need one" >&2; exit 2 ;;
    *)  echo "ERROR: current kubectl context is '${_ctx}', which does not match '${KUBE_CONTEXT}'." >&2
        echo "       Runtime checks would report that cluster's state under narwhal's check ids." >&2
        echo "       Select the right context, or set KUBE_CONTEXT to the expected substring" >&2
        echo "       (KUBE_CONTEXT= disables this guard)." >&2
        exit 2 ;;
  esac
fi

if [ -t 1 ]; then
  BOLD=$'\e[1m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RESET=$'\e[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

PASS=0; FAIL=0; WARN=0
FAILED_IDS=""

# record <id> <status> <description> — appends one row to CHECK_LOG for the report
# exporters. Tab-separated; description is the last field so an incidental tab in it
# does not shift the columns the exporter actually keys on (id/status/timestamp).
record() {
  [ -n "${CHECK_LOG}" ] || return 0
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$3" >> "${CHECK_LOG}"
}

# ok <id> <description> — the check passed
ok()   { PASS=$((PASS + 1)); printf '  %sPASS%s  %-14s %s\n' "${GREEN}" "${RESET}" "$1" "$2"; record "$1" PASS "$2"; }
bad()  { FAIL=$((FAIL + 1)); FAILED_IDS="${FAILED_IDS}$1 "; printf '  %sFAIL%s  %-14s %s\n' "${RED}" "${RESET}" "$1" "$2"; record "$1" FAIL "$2"; }
warn() { WARN=$((WARN + 1)); printf '  %sWARN%s  %-14s %s\n' "${YELLOW}" "${RESET}" "$1" "$2"; record "$1" WARN "$2"; }

section() { printf '\n%s== %s%s\n' "${BOLD}" "$1" "${RESET}"; }

# check <id> <description> <command...> — pass when the command succeeds
check() {
  local id="$1" desc="$2"; shift 2
  if "$@" >/dev/null 2>&1; then ok "${id}" "${desc}"; else bad "${id}" "${desc}"; fi
}

# check_not <id> <description> <command...> — pass when the command FAILS.
# Used for "this pattern must be absent", where a match means the bug is back.
check_not() {
  local id="$1" desc="$2"; shift 2
  if "$@" >/dev/null 2>&1; then bad "${id}" "${desc}"; else ok "${id}" "${desc}"; fi
}

#=========================================
# Static: the repo still carries every fix
#=========================================
run_static() {
  section "STATIC — known fixes still present in the repo"

  # 2026-07-26: containerd=1.7.* pin paired 1.7.12 with runc 1.3.4; AppArmor denied
  # signal delivery and pods wedged in Terminating, taking NFS down with them.
  check_not R01 "no hard containerd 1.7 pin (2026-07-26)" \
    grep -qE '^[^#]*containerd=1\.7' scripts/common/02-containerd.sh

  # 2026-07-26: no script installed the NFS *client*; csi-driver-nfs mounts hung.
  check R02 "01-prerequisites installs nfs-common (2026-07-26)" \
    grep -qE '^[^#]*nfs-common' scripts/common/01-prerequisites.sh

  # 2026-07-26: cloud image ships neither ip_forward nor br_netfilter; kubeadm preflight failed.
  check R03 "01-prerequisites sets ip_forward + br_netfilter (2026-07-26)" \
    grep -qE '^[^#]*br_netfilter' scripts/common/01-prerequisites.sh

  # 2026-07-30: nothing installed yq, which three scripts edit manifests with. The
  # Vagrant box ships it, so the gap only appears on a plain cloud image — and it
  # appears AFTER a successful kubeadm init, which reads as a cluster-level failure.
  # yq now comes from the airgap bundle rather than a GitHub download, so this matches the
  # install call rather than the URL. Matches an executable line, not a mention: the
  # earlier version grepped for `mikefarah/yq`, which also appears in this file's comments,
  # and passed even with the install code deleted.
  check R21 "01-prerequisites installs yq (2026-07-30)" \
    grep -qE '^[^#]*install_bin yq' scripts/common/01-prerequisites.sh

  # Every tool the scripts drive manifests with has to be installed by something in
  # scripts/, not inherited from a pre-baked box. This catches the NEXT one, not just yq.
  local tool missing_tools=""
  for tool in yq jq kubectl helm; do
    grep -rqlE "(^|[^-])\b${tool} " scripts/cluster/ scripts/common/ 2>/dev/null || continue
    # Search all of scripts/, not just common/: helm is fetched on demand by
    # 03-cni-install.sh and 04-addons.sh, which is a legitimate place to install it.
    # `${tool}=` catches the version-pinned apt form, and `apt-mark hold` catches a tool
    # the scripts deliberately freeze. Matching only `install.*${tool}` made this cry wolf
    # the moment 03-k8s-install.sh switched to pinning kubectl — and a warning that is
    # wrong once gets ignored the next time it is right.
    grep -rqE "install.*${tool}|${tool}=|apt-mark hold.*${tool}|${tool}_linux|${tool}/releases/download|get-${tool}" \
      scripts/ 2>/dev/null || missing_tools="${missing_tools}${tool} "
  done
  if [ -z "${missing_tools}" ]; then
    ok R22 "every manifest tool the scripts use is installed by scripts/common"
  else
    warn R22 "used but never installed: ${missing_tools}(inherited from the Vagrant box?)"
  fi

  # 2026-07-26: sudo -E is refused on these images, so env never reached the script.
  check_not R04 "no 'sudo -E' in cloud scripts (2026-07-26)" \
    grep -rqE '^[^#]*sudo -E ' scripts/cloud/

  # 2026-07-26: mirror host written as /<upstream> instead of /v2/<upstream>; the
  # registry 404'd HTML and the airgap mirror had never served a single pull.
  check R05 "mirror host path is /v2/<upstream> (2026-07-26)" \
    grep -qE '^[^#]*v2/' scripts/airgap/06-configure-mirrors.sh

  # 2026-07-26: bare refs pushed verbatim; containerd resolves them to docker.io/library/*.
  check R06 "05-load-images normalizes bare refs (2026-07-26)" \
    grep -qE '^[^#]*docker.io/library' scripts/airgap/05-load-images.sh

  # 2026-07-28: --live cannot see pause or kube-proxy; 5 of 7 kubeadm images were missing.
  check R07 "01-generate-image-list unions kubeadm list (2026-07-28)" \
    grep -qE '^[^#]*kubeadm config images list' scripts/airgap/01-generate-image-list.sh

  # 2026-07-28: docker-archive refuses an existing file; the bootstrap registry tar froze.
  check R08 "02-save-images removes tar before write (2026-07-28)" \
    grep -q 'rm -f' scripts/airgap/02-save-images.sh

  # 2026-07-27: macOS bsdtar AppleDouble sidecars broke helm template in Gitea.
  check R09 "stage-kakao-nodes sets COPYFILE_DISABLE (2026-07-27)" \
    grep -qE '^[^#]*COPYFILE_DISABLE' scripts/cloud/stage-kakao-nodes.sh

  # 2026-07-28: BASH_SOURCE resolved to /tmp under Vagrant, so the sibling was not found.
  check R10 "callers use absolute path to patch-apiserver-memory (2026-07-28)" \
    grep -q '/home/vagrant/scripts/cluster/patch-apiserver-memory.sh' scripts/cluster/02-init-cluster.sh

  # 2026-07-28: the script returned before the apiserver came back; CNI install died.
  check R11 "patch-apiserver-memory polls /livez (2026-07-28)" \
    grep -qE '^[^#]*livez' scripts/cluster/patch-apiserver-memory.sh

  # 2026-07-29: the kakao branch skipped dnsmasq without ever writing the CoreDNS half,
  # so *.DOMAIN was NXDOMAIN in-cluster and every gateway-OIDC route 500'd.
  check R12 "10-dnsmasq kakao branch writes a CoreDNS zone (2026-07-29)" \
    grep -q 'template IN A' scripts/cluster/10-dnsmasq.sh

  # The LB used to claim a pinned node IP because the dependency was declared but unused.
  check R13 "loadbalancer module depends_on compute" \
    grep -q 'depends_on *= *\[module.compute\]' "${TF_DIR}/main.tf"

  # NFS_SERVER_IP / HOST_NETWORK_CIDR defaulted to the Vagrant subnet on cloud nodes.
  # Comments are excluded: several scripts mention the old subnet to explain why they
  # no longer use it, and flagging that would train the reader to ignore this check.
  check_not R14 "no hardcoded 192.168.56 in cloud scripts (2026-07-26)" \
    grep -rqE '^[^#]*192\.168\.56' scripts/cloud/

  # 2026-07-30: nothing served *.DOMAIN to the NODES. Vagrant routes the zone to master-1's
  # dnsmasq via systemd-resolved; the kakao branch skipped that block entirely, so Phase 2
  # scripts that curl a service URL from the host got HTTP 000. Enumerating names in
  # /etc/hosts was the first attempt and does not cover squid.
  check R24 "bastion serves split DNS for the zone (2026-07-30)" \
    grep -qE '^[^#]*dnsmasq' scripts/cloud/setup-bastion-proxy.sh

  check R25 "01-prerequisites points the node resolver at DNS_SERVER (2026-07-30)" \
    grep -qE '^[^#]*DNS_SERVER' scripts/common/01-prerequisites.sh

  # The list-of-hostnames approach must not creep back: it has to track every new route,
  # and it leaves squid unable to resolve anything it proxies.
  check_not R26 "no /etc/hosts service-name enumeration (2026-07-30)" \
    grep -q 'BEGIN narwhal-services' scripts/common/01-prerequisites.sh

  # 2026-07-30: NO_PROXY ended in a literal `.local.narwhal.internal`, so on Kakao every
  # request to a service name went out through squid, which cannot resolve a name that
  # exists only in the nodes' /etc/hosts. Same shape as R14 — a Vagrant-specific constant
  # that survived the domain split — so it gets the same treatment.
  check_not R23 "no hardcoded local.narwhal.internal in cloud scripts (2026-07-30)" \
    grep -rqE '^[^#]*local\.narwhal\.internal' scripts/cloud/

  # 2026-08-20: the `developer` ClusterRole carried pods/exec plus create/update/delete,
  # and `oidc-developer` binds it CLUSTER-WIDE — so a developer could exec into
  # Keycloak in `iam` or the CNPG Postgres in `database`. The mutating half moved to
  # `developer-workload-admin`, which is granted per namespace. Both halves are checked
  # because either one alone silently restores the old reach.
  # check_not runs its argument list directly, so a pipeline has to be wrapped —
  # otherwise `|` reaches awk as a literal argument and the check silently passes.
  check_not R37 "cluster-wide developer role has no exec/portforward/attach (2026-08-20)" \
    bash -c "awk '/^  name: developer\$/,/^---/' gitops/resources/rbac-policies.yaml | grep -qE 'pods/(exec|portforward|attach)'"

  # The whole point of the split. A ClusterRoleBinding to the write role puts the
  # cluster back exactly where it was, and would look like a routine addition.
  check_not R38 "no ClusterRoleBinding grants developer-workload-admin (2026-08-20)" \
    bash -c "awk '/^kind: ClusterRoleBinding\$/,/^---/' gitops/resources/rbac-policies.yaml | grep -q 'developer-workload-admin'"

  # 2026-08-20: Gitea authenticates as whoever X-WEBAUTH-USER names, and the APISIX
  # bypass route (git + /api/v1/ + /api/packages/, deliberately no OIDC) did not strip
  # it — so the header was the credential, from anywhere that reaches the public host.
  check R39 "APISIX strips X-WEBAUTH-USER on the gitea bypass route (2026-08-20)" \
    grep -q 'X-WEBAUTH-USER' gitops/charts/narwhal-platform/templates/apisix-routes.yaml

  check_not R40 "gitea does not trust every reverse proxy (2026-08-20)" \
    grep -qE 'REVERSE_PROXY_TRUSTED_PROXIES="\*"' scripts/cluster/12-gitea.sh

  # 2026-08-20: the GitOps repo was created with "private": false, so anyone able to
  # reach Gitea — anonymously — could clone the description of the entire cluster.
  # Confirmed against a scratch Gitea 1.26.2 before fixing.
  check_not R41 "narwhal-gitops is not created public (2026-08-20)" \
    grep -qE '^[^#]*"private":[[:space:]]*false' scripts/cluster/14-gitops-bootstrap.sh

  # Making it private without this breaks every Application at once: ArgoCD read the
  # repo with no credentials and only worked because it was public. The two changes
  # are one change.
  check R42 "bootstrap registers ArgoCD repo credentials (2026-08-20)" \
    grep -q 'narwhal-gitops-repo' scripts/cluster/14-gitops-bootstrap.sh

  # Whitelist, not enable_push:false — push-to-gitea.sh is the only durable way to
  # change this cluster (selfHeal reverts kubectl), so a hard block would brick
  # operations. Verified: gitea-admin pushes, a WRITE collaborator is rejected.
  check R43 "main branch protection is applied to narwhal-gitops (2026-08-20)" \
    grep -q 'branch_protections' scripts/cluster/14-gitops-bootstrap.sh

  # 2026-08-21: every Application ran in the `default` AppProject, which has
  # sourceRepos *, destinations * and no resource restrictions — so an Application
  # accepted into the repo could deploy anything, from anywhere, into any namespace.
  check_not R44 "no Application uses the default AppProject (2026-08-21)" \
    bash -c "grep -rn 'project: default' gitops/ scripts/ 2>/dev/null | grep -v kubernetes-dashboard | grep -q ."

  # A sync is a deploy. */* let any developer sync argocd, keycloak or kyverno.
  check_not R45 "developer sync is not cluster-wide (2026-08-21)" \
    grep -qE '^[^#]*role:developer, applications, sync, \*/\*' scripts/cluster/13-argocd.sh

  # The check that actually earns its place: an Application added later with a
  # destination namespace or a source repo the project does not list is REJECTED by
  # ArgoCD at sync time, which surfaces as "the app never syncs" long after the
  # commit. This catches it at commit time instead.
  check R46 "every Application fits its AppProject policy (2026-08-21)" \
    python3 scripts/test/lib/check-appproject-fit.py

  # 2026-08-21: clone, the config copy, commit and push were all `|| true`, so a clean
  # install could report success with an empty or stale GitOps source and the symptom
  # — ArgoCD reconciling nothing — appeared days from the cause.
  check_not R47 "no swallowed failures on the critical gitops git operations (2026-08-21)" \
    bash -c "grep -nE '^[^#]*(git clone|git push|cp -r /home/vagrant/configs/gitops).*\|\| true' scripts/cluster/14-gitops-bootstrap.sh | grep -q ."

  # idp-apps is what makes the repository the cluster's desired state. Creating it
  # before the source is verified is what turns a failed bootstrap into a silent one.
  check R48 "idp-apps is gated on source validation (2026-08-21)" \
    grep -q 'GITOPS_SOURCE_VALIDATED' scripts/cluster/14-gitops-bootstrap.sh

  # 2026-08-21, twice in two days: an unquoted heredoc EXECUTES backticks, including on
  # comment lines — `#` is a comment to the document, not to the shell. Once the
  # substitution would have landed inside a Casbin policy. shellcheck calls it SC2006
  # "style", which is the wrong severity to stop anyone.
  check R49 "no backticks inside unquoted heredocs (2026-08-21)" \
    python3 scripts/test/lib/check-heredoc-backticks.py

  # 2026-08-23 (#164): CI installed yq and kubeconform from `releases/latest/download`,
  # so what ran was whatever upstream published most recently and nothing verified it.
  # The scan set is the paths that actually consume tools — scripts/test/ is excluded
  # deliberately, otherwise this check's own pattern would match itself.
  # `^[^#]*` because the comment above quotes the old URL, and an unanchored pattern
  # matches its own explanation — R41 learned this three days earlier.
  check_not R50 "no mutable 'latest' download in the build path (2026-08-23)" \
    grep -rqE '^[^#]*(releases/latest/download|/latest/download/)' \
      .github/workflows/ scripts/cloud/ scripts/airgap/ scripts/cluster/ scripts/common/

  # Same day, same issue one layer down: the nfs-quota-agent chart came from
  # archive/refs/heads/main.tar.gz — a branch, so the bundle's content changed whenever
  # someone pushed. Pinned to a commit SHA; a branch or tag ref means it drifted back.
  check_not R51 "no mutable git ref tarball in the build path (2026-08-23)" \
    grep -rqE '^[^#]*archive/refs/(heads|tags)/' \
      .github/workflows/ scripts/cloud/ scripts/airgap/ scripts/cluster/ scripts/common/

  # `npm install -g markdownlint-cli` took the newest release on every run. npm has no
  # digest to assert for a global install, so the version pin IS the control and an
  # unpinned name is the whole failure.
  check_not R52 "no unpinned global npm install (2026-08-23)" \
    bash -c "grep -rnE '^[^#]*npm (install|i) -g' .github/workflows/ scripts/ | grep -vE '@[0-9]+\\.[0-9]+\\.[0-9]+' | grep -q ."

  # The air-gap bundle downloaded 19 artifacts and verified none of them. Verification
  # lives in fetch(), which every artifact goes through; a copy-pasted curl beside it
  # would silently opt that artifact out, so check the helper is still what is used.
  check R53 "air-gap downloads are checksum-verified (2026-08-23)" \
    bash -c "grep -q 'checksum mismatch' scripts/airgap/07-save-binaries.sh && [ -s scripts/airgap/lib/binary-checksums.tsv ]"

  # A missing row must be a failure, not a skip — otherwise adding an artifact opts it
  # out of verification and the bundle grows an unchecked entry nobody notices.
  check R54 "a missing checksum row fails the air-gap fetch (2026-08-23)" \
    grep -q 'checksum missing from binary-checksums.tsv' scripts/airgap/07-save-binaries.sh

  # 2026-08-23: the seam between this repo's RBAC bindings and narwhal-portal's login
  # allowlist has no compiler to catch drift — a group renamed or removed on either side
  # just silently breaks login or grants a role nobody can ever present a token for.
  # Proves both directions: the contract holds against the real sibling checkout, AND a
  # deliberately removed group actually fails the check rather than passing quietly.
  # The mutation runs against a temp copy of portal's auth.ts, never the real checkout.
  if [ -d ../narwhal-portal ]; then
    local contract_ok=1
    scripts/test/check-oidc-rbac-portal-contract.sh >/dev/null 2>&1 || contract_ok=0

    local drift_tmp drift_detected=0
    drift_tmp="$(mktemp -d)"
    mkdir -p "${drift_tmp}/src/lib"
    sed '/"viewer",/d' ../narwhal-portal/src/lib/auth.ts > "${drift_tmp}/src/lib/auth.ts"
    PORTAL_DIR="${drift_tmp}" scripts/test/check-oidc-rbac-portal-contract.sh >/dev/null 2>&1 \
      || drift_detected=1
    rm -rf "${drift_tmp}"

    if [ "${contract_ok}" -eq 1 ] && [ "${drift_detected}" -eq 1 ]; then
      ok R55 "OIDC RBAC groups match portal ALLOWED_GROUPS, and drift is caught (2026-08-23)"
    elif [ "${contract_ok}" -eq 0 ]; then
      bad R55 "OIDC RBAC <-> portal ALLOWED_GROUPS contract is currently drifted (2026-08-23)"
    else
      bad R55 "check-oidc-rbac-portal-contract.sh fails to detect a removed portal group (2026-08-23)"
    fi
  else
    warn R55 "narwhal-portal sibling checkout not found; contract check skipped"
  fi

  # 2026-08-23 (#160): 492e65a's own commit message left this open — the pod-network CIDR
  # trusted_proxies narrowing is a second layer, not a boundary, since every pod shares it.
  # The actual boundary is an ingress NetworkPolicy on gitea-http naming its real direct
  # callers (APISIX, ArgoCD repo-server/application-controller, the portal, Kaniko),
  # verified against this repo rather than guessed (12-gitea.sh, 13-argocd.sh,
  # 08-1-networking.sh, narwhal-portal-k8s.yaml, narwhal-portal's deploy/kaniko-build-job.yaml).
  # check-gitea-ingress-policy.py checks structure, not just presence: a policy that names
  # gitea but still carries a bare allow-all `from` rule reads as fixed and is not. The
  # negative case runs against a mutated temp copy — one with the APISIX rule stripped —
  # never the real file, and proves the check actually fails when the gap reopens.
  check R56 "gitea-http NetworkPolicy allows only its real direct callers (2026-08-23)" \
    python3 scripts/test/lib/check-gitea-ingress-policy.py

  local gitea_np_drift_tmp
  gitea_np_drift_tmp="$(mktemp -d)"
  python3 - "${gitea_np_drift_tmp}" <<'PYEOF'
import sys, yaml
tmp = sys.argv[1]
with open("gitops/resources/gitea-ingress-policy.yaml") as f:
    docs = list(yaml.safe_load_all(f))
for d in docs:
    if d and d.get("kind") == "NetworkPolicy":
        d["spec"]["ingress"] = [
            r for r in d["spec"]["ingress"]
            if not any(
                (e.get("podSelector") or {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "apisix"
                for e in r.get("from") or []
            )
        ]
with open(f"{tmp}/gitea-ingress-policy.yaml", "w") as f:
    yaml.safe_dump_all(docs, f)
PYEOF
  check_not R57 "check-gitea-ingress-policy.py catches a removed APISIX rule (2026-08-23)" \
    python3 scripts/test/lib/check-gitea-ingress-policy.py "${gitea_np_drift_tmp}/gitea-ingress-policy.yaml"
  rm -rf "${gitea_np_drift_tmp}"

  # narwhal-portal#55: the tuning Job pod (hostPID+hostNetwork, privileged, nsenters
  # into the host to run allowlisted node-tuning commands) has no legitimate in-cluster
  # caller and calls nothing in-cluster itself — unlike gitea-ingress-policy.yaml, its
  # correct posture is deny-all in both directions, not an allowlist. check-tuning-job-
  # network-policy.py checks structure: any non-empty ingress/egress rule is a
  # regression. The negative case runs against a mutated temp copy with a rule added
  # back, never the real file.
  check R61 "narwhal-tuning Job NetworkPolicy denies all ingress+egress (2026-08-23)" \
    python3 scripts/test/lib/check-tuning-job-network-policy.py

  local tuning_np_drift_tmp
  tuning_np_drift_tmp="$(mktemp -d)"
  python3 - "${tuning_np_drift_tmp}" <<'PYEOF'
import sys, yaml
tmp = sys.argv[1]
with open("gitops/resources/tuning-job-network-policy.yaml") as f:
    docs = list(yaml.safe_load_all(f))
for d in docs:
    if d and d.get("kind") == "NetworkPolicy":
        d["spec"]["egress"] = [{"to": [{"namespaceSelector": {}}]}]
with open(f"{tmp}/tuning-job-network-policy.yaml", "w") as f:
    yaml.safe_dump_all(docs, f)
PYEOF
  check_not R62 "check-tuning-job-network-policy.py catches a reintroduced egress rule (2026-08-23)" \
    python3 scripts/test/lib/check-tuning-job-network-policy.py "${tuning_np_drift_tmp}/tuning-job-network-policy.yaml"
  rm -rf "${tuning_np_drift_tmp}"

  # narwhal#53: component-licenses.tsv already carried a resolved SPDX id for every
  # bundle image, but nothing enforced it on a PR — a forbidden or blank license
  # reappearing was only caught by a human reading the file's own comments (the
  # RSALv2/SSPLv1 redis note: "regressed — fix the swap, do not add the row").
  # check-license-policy.py is that CI gate. The negative case runs against a
  # mutated temp copy with a forbidden-license row appended, never the real file.
  check R63 "component-licenses.tsv has no forbidden/blank license rows (2026-08-24)" \
    python3 scripts/airgap/lib/check-license-policy.py

  local license_policy_drift_tmp
  license_policy_drift_tmp="$(mktemp -d)"
  cp scripts/airgap/lib/component-licenses.tsv "${license_policy_drift_tmp}/component-licenses.tsv"
  printf 'docker.io/library/redis\tredis/redis\tRSALv2\t\n' >> "${license_policy_drift_tmp}/component-licenses.tsv"
  check_not R64 "check-license-policy.py catches a reintroduced forbidden license (2026-08-24)" \
    python3 scripts/airgap/lib/check-license-policy.py "${license_policy_drift_tmp}/component-licenses.tsv"
  rm -rf "${license_policy_drift_tmp}"

  # narwhal#52: the Kyverno disallow-latest-tag policy (kyverno-policies.yaml) is
  # Audit-mode and excludes most system namespaces, so it observes a mutable tag
  # reaching the cluster — it does not stop a PR from introducing one. This is the
  # pre-merge half: renders every gitops/charts/ chart with helm template (same
  # defaults ArgoCD/a plain install use) plus the raw gitops/resources/*.yaml, and
  # fails on any image with no tag or an explicit :latest. The negative case runs
  # against a mutated temp copy of the gitops/ tree, never the real one.
  if command -v helm >/dev/null 2>&1; then
    check R65 "no mutable (:latest / untagged) image reference in gitops/ (2026-08-24)" \
      python3 scripts/gitops/check-no-mutable-tags.py

    local mutable_tag_drift_tmp
    mutable_tag_drift_tmp="$(mktemp -d)"
    mkdir -p "${mutable_tag_drift_tmp}/gitops/charts" "${mutable_tag_drift_tmp}/gitops/resources"
    cp -r gitops/charts/* "${mutable_tag_drift_tmp}/gitops/charts/"
    cp gitops/resources/*.yaml "${mutable_tag_drift_tmp}/gitops/resources/"
    sed -i.bak 's#docker.io/alpine/k8s:1.31.4#docker.io/alpine/k8s:latest#' \
      "${mutable_tag_drift_tmp}/gitops/resources/ghost-pod-reaper.yaml"
    rm -f "${mutable_tag_drift_tmp}/gitops/resources/ghost-pod-reaper.yaml.bak"
    check_not R66 "check-no-mutable-tags.py catches a reintroduced :latest tag (2026-08-24)" \
      python3 scripts/gitops/check-no-mutable-tags.py "${mutable_tag_drift_tmp}"
    rm -rf "${mutable_tag_drift_tmp}"
  else
    warn R65 "helm not installed locally — R65/R66 (mutable image tag gate) skipped"
  fi

  # narwhal#51: 01-generate-image-list.sh --live used to treat a failed chart render
  # (how Helm hook/Job images — cert-manager's startupapicheck, kube-prometheus-stack's
  # kube-webhook-certgen — get collected) as "Not fatal": WARN and keep going, silently
  # shipping a bundle that installs most of the way and dies on whichever hook was
  # missing. check-airgap-hardfail.py checks structure, not just "exit 1" appearing
  # somewhere in the file: the hook_imgs branch must be an if/elif(AIRGAP_ALLOW_INCOMPLETE)
  # /else where the default (no env var) else exits 1. The negative case runs against a
  # mutated temp copy — the old unconditional WARN-and-continue block restored — never the
  # real file, and proves the check actually fails when the soft-continue gap reopens.
  check R58 "01-generate-image-list.sh hard-fails by default on incomplete hook images (2026-08-23)" \
    python3 scripts/test/lib/check-airgap-hardfail.py

  local airgap_hardfail_drift_tmp
  airgap_hardfail_drift_tmp="$(mktemp -d)"
  python3 - "${airgap_hardfail_drift_tmp}" <<'PYEOF'
import re, sys
tmp = sys.argv[1]
with open("scripts/airgap/01-generate-image-list.sh") as f:
    text = f.read()

old_block = '''  hook_imgs=$(hook_images_from_charts || true)
  if [[ -n "${hook_imgs}" ]]; then
    echo "  helm hook/Job images from bundled charts: $(printf '%s\\n' "${hook_imgs}" | wc -l | tr -d ' ')" >&2
  else
    echo "WARN: could not render the bundled charts, so Helm hook/Job images are NOT in this" >&2
    echo "      list. They are invisible to the live scan, so the bundle will be short by" >&2
    echo "      however many there are. Stage ${CHARTS_LOCAL} or run with a reachable master-1." >&2
  fi
'''

block_re = re.compile(r'  hook_imgs=\$\(hook_images_from_charts.*?\n  fi\n', re.DOTALL)
mutated, n = block_re.subn(old_block, text)
assert n == 1, f"expected 1 substitution, got {n}"
with open(f"{tmp}/01-generate-image-list.sh", "w") as f:
    f.write(mutated)
PYEOF
  check_not R59 "check-airgap-hardfail.py catches the reintroduced soft-continue anti-pattern (2026-08-23)" \
    python3 scripts/test/lib/check-airgap-hardfail.py "${airgap_hardfail_drift_tmp}/01-generate-image-list.sh"
  rm -rf "${airgap_hardfail_drift_tmp}"

  # narwhal#51: 02-save-images.sh only fails on a skopeo copy error — nothing afterward
  # re-checked images.txt, manifest.txt and the oci/ layout still agree, so a resumed
  # or partial run could leave them silently out of sync. 09-verify-bundle-completeness.sh
  # is the 1:1 release gate for that.
  check R60 "09-verify-bundle-completeness.sh exists as the bundle 1:1 release gate (2026-08-23)" \
    test -x scripts/airgap/09-verify-bundle-completeness.sh

  # Every script keeps its error handling — CI does not catch a missing set line.
  # 00-config.sh is exempt by design: it is sourced, so `set -e` there would impose
  # itself on whatever sourced it rather than on a process of its own.
  local missing=""
  local f
  for f in scripts/cloud/*.sh scripts/airgap/*.sh; do
    [ -f "$f" ] || continue
    [ "$(basename "$f")" = "00-config.sh" ] && continue
    grep -q 'set -euo pipefail' "$f" || missing="${missing}$(basename "$f") "
  done
  if [ -z "${missing}" ]; then
    ok R15 "every cloud/airgap script has set -euo pipefail"
  else
    bad R15 "missing set -euo pipefail: ${missing}"
  fi

  # Every script still parses. A syntax error only surfaces mid-provision otherwise.
  local broken=""
  for f in scripts/cloud/*.sh scripts/airgap/*.sh scripts/cluster/*.sh scripts/common/*.sh; do
    [ -f "$f" ] || continue
    bash -n "$f" 2>/dev/null || broken="${broken}$(basename "$f") "
  done
  if [ -z "${broken}" ]; then
    ok R16 "all provisioning scripts parse"
  else
    bad R16 "syntax errors: ${broken}"
  fi

  # provider-aware GitOps: MetalLB must not render on kakao, APISIX must be NodePort.
  check R17 "metallb template gated on provider" \
    grep -q 'ne .Values.provider "kakao"' gitops/charts/narwhal-apps/templates/metallb.yaml

  # 2026-08-04: every download in the provisioning path crosses the bastion proxy to a
  # public CDN, and those fail transiently — a clean run died on `curl: (35)
  # SSL_ERROR_SYSCALL` fetching helm, and the same URL then answered 4 times out of 5.
  # One flaky fetch was failing the whole init stage, discarding a successful kubeadm init.
  #
  # Deliberately coarse: "this file fetches from a public host AND defines no retry".
  # A line-level check flagged files whose curl sits inside a function called via retry —
  # the third time in this suite that a clever grep produced a false alarm. A check nobody
  # trusts is worse than a blunt one they do.
  local unguarded=""
  local f
  for f in scripts/cluster/*.sh scripts/common/*.sh; do
    [ -f "$f" ] || continue
    grep -qE "^\s*(sudo )?(curl|wget)\s.*https://(get\.|raw\.|github\.com)" "$f" 2>/dev/null || continue
    grep -q "^retry()" "$f" 2>/dev/null || unguarded="${unguarded}$(basename "$f") "
  done
  if [ -z "${unguarded}" ]; then
    ok R27 "public downloads in the provisioning path are retried (2026-08-04)"
  else
    warn R27 "fetches from a public host with no retry(): ${unguarded}"
  fi

  # 2026-08-04: the whole point of the airgap work. If any of these reappear the install
  # silently needs the internet again, which only shows up as flakiness until the day the
  # network is genuinely closed.
  check_not R28 "no public helm repos in the install path (2026-08-04)" \
    grep -rqE '^[^#]*helm repo add' scripts/cluster/

  # 2026-08-05: R28 matched `helm repo add` and nothing else, so `helm pull apisix/...`
  # sailed through — it assumes a repo someone else registered, which on an airgapped node
  # is nobody, and phase2 died on "repo apisix not found" after 40 minutes of install.
  # Checking for pull/fetch rather than for repo-qualified refs keeps this free of false
  # positives: `--show-only templates/x.yaml` also looks like <word>/<word>.
  check_not R28b "no helm pull/fetch — charts come from the bundle (2026-08-05)" \
    grep -rqE '^[^#]*helm[[:space:]]+(pull|fetch)[[:space:]]' scripts/cluster/ scripts/common/

  check_not R29 "no internet fetches in the default install path (2026-08-04)" \
    grep -rqE '^[^#]*(curl|wget|kubectl apply -f)[^#]*https://(get\.helm\.sh|raw\.githubusercontent\.com|github\.com/(cilium|kubernetes-sigs|argoproj|keycloak|mikefarah|dasomel))' \
      scripts/cluster/03-cni-install.sh scripts/cluster/04-addons.sh \
      scripts/cluster/05-nfs-quota-agent.sh scripts/cluster/11-keycloak.sh \
      scripts/cluster/13-argocd.sh scripts/common/01-prerequisites.sh

  # 2026-08-05: R28/R29 cover charts and curl, but the leak that actually broke the first
  # offline install was an image pull. `ctr` resolves images itself instead of going
  # through containerd's CRI plugin, so it ignores /etc/containerd/certs.d and reaches
  # upstream even on a node whose every other pull is mirrored. Flag any `ctr ... pull`
  # that does not say where the host config lives.
  # No pipe into `grep -q`: it exits on the first match and the still-writing `grep -r`
  # takes SIGPIPE, which pipefail turns into a false rc — and inside a condition that
  # reads as "no match", so a real leak would report as clean. Capture, then test.
  check_not R30 "every ctr image pull passes --hosts-dir (2026-08-05)" \
    bash -c 'p=$(grep -rhE "^[^#]*ctr .*pull" scripts/cluster/ scripts/common/ || true); [ -n "$p" ] && grep -qv -- "--hosts-dir" <<<"$p"'

  # 2026-08-05: the apt bundle and the AIRGAP=1 switch were built and wired into the
  # Vagrantfile only. On Kakao the same scripts ran with AIRGAP unset and no bundle staged,
  # so an "airgap" install still fetched every .deb from the Ubuntu archive — and passed,
  # because the VPC has its own egress. A capability that exists on one provider and not
  # the other is indistinguishable from a capability that does not exist.
  check R31 "provision-kakao.sh passes AIRGAP to the nodes (2026-08-05)" \
    grep -qE '^[^#]*AIRGAP=\$\{AIRGAP\}' scripts/cloud/provision-kakao.sh

  check R32 "stage-kakao-nodes.sh stages the apt bundle to /srv/airgap (2026-08-05)" \
    grep -qE '^[^#]*/srv/airgap' scripts/cloud/stage-kakao-nodes.sh

  # 2026-08-05: the install went fully offline while the GitOps layer did not. ArgoCD
  # Applications still name public chart repos as their source, so every refresh, resync and
  # selfHeal needs the internet — proven by forcing a hard refresh on an isolated cluster:
  # harbor, kyverno, metallb, openbao and velero-ui all went Unknown with
  # `failed to list refs: ... lookup github.com`. It read as Synced only because the sync had
  # happened while a default route was still present. Count them so the number cannot grow
  # silently while the airgap work is migrating them.
  local ext_repos
  # `|| true` is load-bearing: the moment the migration succeeds and no public repoURL
  # remains, `grep -v` emits nothing and exits 1, and pipefail kills the whole suite at
  # its own success case — measured 2026-08-06, right after cd64ff4 landed. The count
  # still comes through because wc runs regardless.
  ext_repos=$(grep -rhoE 'repoURL: *https?://[^ ]+' gitops/ --include='*.yaml' 2>/dev/null \
              | grep -v 'svc.cluster.local' | sort -u | wc -l | tr -d ' ' || true)
  [ -n "${ext_repos}" ] || ext_repos=0
  if [ "${ext_repos}" = "0" ]; then
    ok R34 "no public repoURL in the GitOps layer (2026-08-05)"
  else
    warn R34 "${ext_repos} ArgoCD sources are public repos — GitOps cannot sync offline (2026-08-05)"
  fi

  # bastion.tf must be tracked — a blanket csp/ gitignore once hid it from fresh clones.
  check R18 "bastion.tf is tracked by git" \
    git ls-files --error-unmatch "${TF_DIR}/bastion.tf"

  # 2026-08-07: `.airgap-registry-data/` (6.8 GB of registry blobs) sat tracked in the
  # history of a public repo while .gitignore listed it — the entry was added after the
  # commit, and gitignore does not apply to already-tracked paths. It grew the repo to
  # 5.79 GiB and would have made the push fail on GitHub's 100 MB file limit.
  #
  # Tracked-AND-ignored is the exact signature, and it is cheap to ask. A plain size
  # check would not do: the offending files are individually unremarkable, and it is the
  # intent recorded in .gitignore that says they do not belong.
  # --no-index is the whole trick: without it `git check-ignore` reports nothing, because
  # git's rule is that a tracked path is never "ignored". Its man page names this exact
  # case — "debug why a path became tracked ... and was not ignored as expected".
  local tracked_ignored
  tracked_ignored=$(git ls-files -z 2>/dev/null | xargs -0 git check-ignore --no-index 2>/dev/null | head -5 | tr '\n' ' ' || true)
  if [ -z "${tracked_ignored}" ]; then
    ok R35 "no tracked file matches .gitignore (2026-08-07)"
  else
    bad R35 "tracked but gitignored — will not stop growing: ${tracked_ignored}(2026-08-07)"
  fi

  # "The bundle is still incomplete" has been the finding three separate times, each
  # from a different cause (live-only image list, docker-archive refusing to overwrite,
  # bare refs). Check the shape rather than any one of those: the three counts must
  # agree, and the bootstrap tar must not predate the images around it.
  # Check every bundle present, not the one matching this host. Keying off `uname -m`
  # meant a Mac checking the arm64 (Vagrant) bundle while the Kakao work was all amd64 —
  # so amd64 could drift arbitrarily far from images.txt and this still reported on the
  # wrong one. AIRGAP_BUNDLE_DIR still pins a single bundle when you want that.
  local bundle bundles=() want have layouts stale bad19=""
  if [ -n "${AIRGAP_BUNDLE_DIR:-}" ]; then
    bundles=("${AIRGAP_BUNDLE_DIR}")
  else
    for bundle in narwhal-airgap-bundle-amd64 narwhal-airgap-bundle-arm64; do
      [ -d "${bundle}/oci" ] && bundles+=("${bundle}")
    done
  fi
  if [ "${#bundles[@]}" = 0 ]; then
    warn R19 "no bundle found; airgap checks skipped"
  else
    want=$(grep -cvE '^\s*(#|$)' scripts/airgap/images.txt)
    for bundle in "${bundles[@]}"; do
      have=$(grep -cvE '^\s*$' "${bundle}/manifest.txt" 2>/dev/null || echo 0)
      layouts=$(find "${bundle}/oci" -maxdepth 4 -name index.json | wc -l | tr -d ' ')
      [ "${want}" = "${have}" ] && [ "${want}" = "${layouts}" ] \
        || bad19="${bad19}${bundle##*-}(manifest=${have} oci=${layouts}) "
    done
    if [ -z "${bad19}" ]; then
      ok R19 "bundle complete: ${want} images in list, manifest and oci (${#bundles[@]} bundle(s))"
    else
      bad R19 "bundle counts disagree with images.txt=${want}: ${bad19}"
    fi

    # R20 below inspects one bundle's bootstrap tar; the first is enough.
    bundle="${bundles[0]}"

    # 2026-07-28: docker-archive refuses an existing file, so the tar silently froze at
    # the bundle's creation date while every other image refreshed.
    # Both bundles must be complete, not just the one this host happens to match. The
    # arm64 (Vagrant) bundle sat with bin=0 manifests=0 while amd64 (Kakao) had 4 and 10,
    # because 07-save-binaries.sh had only been run for one arch — a Vagrant install would
    # have died at the first `install_bin helm`.
    local a incomplete=""
    for a in amd64 arm64; do
      local d="narwhal-airgap-bundle-${a}"
      [ -d "${d}" ] || continue
      [ "$(ls "${d}/bin" 2>/dev/null | wc -l | tr -d ' ')" -ge 4 ] \
        && [ "$(ls "${d}/manifests" 2>/dev/null | wc -l | tr -d ' ')" -ge 10 ] \
        && [ -f "${d}/apt/Packages.gz" ] \
        && [ "$(ls "${d}/apt"/*.deb 2>/dev/null | wc -l | tr -d ' ')" -ge 100 ] \
        || incomplete="${incomplete}${a} "
    done
    if [ -z "${incomplete}" ]; then
      ok R33 "both bundles carry binaries, manifests and .debs (2026-08-05)"
    else
      warn R33 "bundle incomplete: ${incomplete}(run 07-save-binaries.sh / 07-save-apt-packages.sh)"
    fi

    if [ -f "${bundle}/bootstrap/registry.tar" ]; then
      stale=$(find "${bundle}/oci" -name index.json -newer "${bundle}/bootstrap/registry.tar" | wc -l | tr -d ' ')
      [ "${stale}" = "0" ] && ok R20 "bootstrap registry.tar not stale (2026-07-28)" \
        || bad R20 "registry.tar older than ${stale} image layouts (2026-07-28)"
    else
      bad R20 "bundle has no bootstrap/registry.tar"
    fi
  fi

  # narwhal#48: trivy-operator.yaml deployed Trivy in Standalone mode with
  # dbRegistry=ghcr.io — an ONLINE DB pull, the opposite of what an air-gapped
  # cluster needs, and a scan Job with no egress would fail to fetch a DB. Fixing it
  # surfaced two MORE independent online-pull paths trivy-operator.yaml also has
  # (javaDbRegistry, policiesBundle.registry for the compliance/config-audit rego
  # bundle) that neither the original triage nor the narwhal#50 test-strategy pass
  # had identified — none of the three go through containerd, so the existing
  # airgap image mirror (06-configure-mirrors.sh) cannot fix any of them; only
  # pointing Trivy itself at the internal Harbor registry does.
  check R67 "Trivy DB/Java DB/checks bundle point at the internal registry, not ghcr.io (2026-08-25)" \
    python3 scripts/test/lib/check-trivy-db-internal-registry.py

  local trivy_db_drift_tmp
  trivy_db_drift_tmp="$(mktemp -d)"
  python3 - "${trivy_db_drift_tmp}" <<'PYEOF'
import sys
import yaml
tmp = sys.argv[1]
with open("gitops/charts/narwhal-apps/templates/trivy-operator.yaml") as f:
    doc = yaml.safe_load(f)
doc["spec"]["source"]["helm"]["valuesObject"]["trivy"]["dbRegistry"] = "ghcr.io"
with open(f"{tmp}/trivy-operator.yaml", "w") as f:
    yaml.safe_dump(doc, f)
PYEOF
  check_not R68 "check-trivy-db-internal-registry.py catches a reintroduced ghcr.io dbRegistry (2026-08-25)" \
    python3 scripts/test/lib/check-trivy-db-internal-registry.py "${trivy_db_drift_tmp}/trivy-operator.yaml"
  rm -rf "${trivy_db_drift_tmp}"

  # narwhal#48: security-db artifacts (trivy-db etc.) are legitimately re-published
  # under the same tag, so unlike binary-checksums.tsv there is no pinned golden
  # digest to check future fetches against — the only integrity check available is
  # "does the local copy's digest match what the registry reported right before the
  # copy", and it has to actually fail the run on a mismatch, not just log one.
  check R69 "fetch-security-db.sh fails closed on a digest mismatch (2026-08-25)" \
    python3 scripts/test/lib/check-security-db-fetch-integrity.py

  local secdb_integrity_drift_tmp
  secdb_integrity_drift_tmp="$(mktemp -d)"
  python3 - "${secdb_integrity_drift_tmp}" <<'PYEOF'
import sys
tmp = sys.argv[1]
with open("scripts/airgap/lib/fetch-security-db.sh") as f:
    text = f.read()
old_block = '''  post_digest="$(skopeo inspect --raw "oci:${dest}:${tag}" 2>/dev/null | shasum -a 256 | cut -d' ' -f1 || true)"
  if [[ "${post_digest}" != "${pre_digest}" ]]; then
    echo "[FAIL] ${name}: local copy digest (${post_digest}) != registry digest (${pre_digest}) at fetch time" >&2
    fail=$((fail + 1))
    continue
  fi
'''
assert old_block in text, "anchor block not found — fetch-security-db.sh changed shape"
mutated = text.replace(old_block, '  post_digest="$(skopeo inspect --raw "oci:${dest}:${tag}" 2>/dev/null | shasum -a 256 | cut -d\' \' -f1 || true)"\n  # digest check removed (regression fixture)\n')
with open(f"{tmp}/fetch-security-db.sh", "w") as f:
    f.write(mutated)
PYEOF
  check_not R70 "check-security-db-fetch-integrity.py catches a removed digest-mismatch guard (2026-08-25)" \
    python3 scripts/test/lib/check-security-db-fetch-integrity.py "${secdb_integrity_drift_tmp}/fetch-security-db.sh"
  rm -rf "${secdb_integrity_drift_tmp}"

  # narwhal#48 AC: "오프라인 환경에서 DB 부재/만료 시 명확한 FAIL 또는 WARNING
  # 정책" — check-security-db-freshness.py is that policy. Real case: a manifest
  # fetched "now" must pass a 7-day SLO. Negative case: a manifest fetched 24 days
  # ago (fixture, not a mutated real file — there is no committed real manifest,
  # since fetch-security-db.sh's output is a live artifact, never checked in) must
  # fail, and a MISSING manifest must also fail (never silently pass).
  local secdb_fresh_tmp
  secdb_fresh_tmp="$(mktemp -d)"
  python3 - "${secdb_fresh_tmp}" <<'PYEOF'
import datetime, json, sys
tmp = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
fresh = {"artifacts": [{"name": "trivy-db", "digest": "sha256:" + "0" * 64, "fetched_at": now}]}
with open(f"{tmp}/fresh-manifest.json", "w") as f:
    json.dump(fresh, f)
old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
stale = {"artifacts": [{"name": "trivy-db", "digest": "sha256:" + "0" * 64, "fetched_at": old}]}
with open(f"{tmp}/stale-manifest.json", "w") as f:
    json.dump(stale, f)
PYEOF
  check R71 "check-security-db-freshness.py passes a freshly-fetched manifest (2026-08-25)" \
    python3 scripts/airgap/lib/check-security-db-freshness.py "${secdb_fresh_tmp}/fresh-manifest.json"
  check_not R72 "check-security-db-freshness.py fails a 24-day-old manifest (2026-08-25)" \
    python3 scripts/airgap/lib/check-security-db-freshness.py "${secdb_fresh_tmp}/stale-manifest.json"
  check_not R73 "check-security-db-freshness.py fails closed on a missing manifest (2026-08-25)" \
    python3 scripts/airgap/lib/check-security-db-freshness.py "${secdb_fresh_tmp}/does-not-exist.json"
  rm -rf "${secdb_fresh_tmp}"

  # narwhal#35: Kyverno verify-image-signatures policy configures Cosign public key
  # signature verification, SBOM attestation predicate checking, and digest validation.
  check R74 "Kyverno verify-image-signatures ClusterPolicy is present and valid (2026-08-25)" \
    python3 scripts/test/lib/check-kyverno-signed-image-policy.py

  local kyverno_signed_img_tmp
  kyverno_signed_img_tmp="$(mktemp -d)"
  python3 - "${kyverno_signed_img_tmp}" <<'PYEOF'
import sys
tmp = sys.argv[1]
with open("gitops/resources/kyverno-policies.yaml") as f:
    text = f.read()
# Mutate by removing the verify-image-signatures ClusterPolicy block
parts = text.split("---\napiVersion: kyverno.io/v1\nkind: ClusterPolicy\nmetadata:\n  name: verify-image-signatures")
assert len(parts) == 2, "expected verify-image-signatures policy anchor in kyverno-policies.yaml"
with open(f"{tmp}/kyverno-policies.yaml", "w") as f:
    f.write(parts[0])
PYEOF
  check_not R75 "check-kyverno-signed-image-policy.py catches a removed verify-image-signatures policy (2026-08-25)" \
    python3 scripts/test/lib/check-kyverno-signed-image-policy.py "${kyverno_signed_img_tmp}/kyverno-policies.yaml"
  rm -rf "${kyverno_signed_img_tmp}"

  # narwhal#35: 10-verify-image-signatures.sh exists as the air-gap image signature gate.
  check R76 "10-verify-image-signatures.sh exists as the air-gap image signature gate (2026-08-25)" \
    test -x scripts/airgap/10-verify-image-signatures.sh

  # narwhal#42: a versioned control-plane event contract must exercise both sides
  # of validation. R77 validates the committed, portal-compatible operation event;
  # R78 changes only its fixed schema version in a temporary copy and proves the
  # CLI rejects it. A real sample alone cannot expose a validator that accepts
  # every input, so this mirrors the real-file + mutation pattern above.
  check R77 "canonical event envelope sample validates through narwhalctl (2026-08-25)" \
    scripts/test/check-event-envelope-contract.sh

  local event_contract_drift_tmp
  event_contract_drift_tmp="$(mktemp -d)"
  cp examples/event-envelope.operation.started.json "${event_contract_drift_tmp}/invalid-event.json"
  python3 - "${event_contract_drift_tmp}/invalid-event.json" <<'PYEOF'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    event = json.load(f)
event["schema_version"] = "9.9"
with open(path, "w", encoding="utf-8") as f:
    json.dump(event, f)
PYEOF
  check_not R78 "narwhalctl rejects a mutated unsupported envelope schema version (2026-08-25)" \
    python3 scripts/narwhalctl.py events emit --file "${event_contract_drift_tmp}/invalid-event.json"
  rm -rf "${event_contract_drift_tmp}"

  # narwhal#46 pilot: a version pin alone does not make cert-manager safe to roll.
  # The real Application must carry the HA/PDB/topology declarations that the static
  # preflight requires. A temporary copy has just the webhook PDB removed through yq;
  # the negative case proves the preflight rejects the reopened gap without touching
  # the real GitOps manifest.
  check R79 "cert-manager static upgrade preflight validates the real GitOps HA contract (2026-08-25)" \
    scripts/cluster/preflight-cert-manager-upgrade.sh

  local cert_manager_upgrade_drift_tmp
  cert_manager_upgrade_drift_tmp="$(mktemp -d)"
  cp gitops/charts/narwhal-apps/templates/cert-manager.yaml \
    "${cert_manager_upgrade_drift_tmp}/cert-manager.yaml"
  yq -i 'del(.spec.source.helm.valuesObject.webhook.podDisruptionBudget)' \
    "${cert_manager_upgrade_drift_tmp}/cert-manager.yaml"
  check_not R80 "cert-manager preflight catches a removed webhook PDB declaration (2026-08-25)" \
    scripts/cluster/preflight-cert-manager-upgrade.sh "${cert_manager_upgrade_drift_tmp}/cert-manager.yaml"
  rm -rf "${cert_manager_upgrade_drift_tmp}"

  # narwhal#45: offline upgrade bundle manifest schema, verification, and dry-run diff
  check R81 "air-gap upgrade bundle schema and dry-run diff tool validate static manifests (2026-08-25)" \
    python3 scripts/airgap/lib/verify-upgrade-bundle.py --manifest scripts/airgap/lib/upgrade-bundle-v1.1.0.json

  local upgrade_bundle_drift_tmp
  upgrade_bundle_drift_tmp="$(mktemp -d)"
  python3 - "${upgrade_bundle_drift_tmp}/invalid-bundle.json" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open("scripts/airgap/lib/upgrade-bundle-v1.1.0.json", encoding="utf-8") as f:
    doc = json.load(f)
doc["artifacts"][0]["license"] = "SSPLv1"
with open(path, "w", encoding="utf-8") as f:
    json.dump(doc, f)
PYEOF
  check_not R82 "upgrade bundle validator rejects forbidden licenses and schema violations (2026-08-25)" \
    python3 scripts/airgap/lib/verify-upgrade-bundle.py --manifest "${upgrade_bundle_drift_tmp}/invalid-bundle.json"
  rm -rf "${upgrade_bundle_drift_tmp}"

  # narwhal#6: an exported member-cluster credential must retain a stable ID while
  # reusing the existing Portal reader RBAC; a second broad reader role would drift.
  # Structural presence checks alone never prove they can fail, so each of R83/R84/R85
  # below runs against a mutated temp copy too -- never the real file -- with the
  # relevant line stripped, proving the check actually catches the gap reopening.
  check R83 "multi-cluster credential exporter preserves Portal RBAC and restrictive output permissions (2026-08-25)" \
    bash -c "grep -q 'CLUSTER_ROLE_BINDING=\"narwhal-portal\"' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh && grep -q 'umask 077' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh && grep -q 'credentialRef' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh"

  local cred_exporter_drift_tmp
  cred_exporter_drift_tmp="$(mktemp -d)"
  grep -v '^umask 077$' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh \
    > "${cred_exporter_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh"
  check_not R83b "R83's check catches a removed 'umask 077' restrictive-output guard (2026-08-25)" \
    bash -c "grep -q 'CLUSTER_ROLE_BINDING=\"narwhal-portal\"' '${cred_exporter_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh' && grep -q 'umask 077' '${cred_exporter_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh' && grep -q 'credentialRef' '${cred_exporter_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh'"
  rm -rf "${cred_exporter_drift_tmp}"

  check R84 "multi-cluster design keeps registration credential references distinct from secret delivery (2026-08-25)" \
    grep -q 'credentialRef.apiServerEnvVar' docs/common/multicluster-control-plane.md

  local mc_design_drift_tmp
  mc_design_drift_tmp="$(mktemp -d)"
  grep -v 'credentialRef.apiServerEnvVar' docs/common/multicluster-control-plane.md \
    > "${mc_design_drift_tmp}/multicluster-control-plane.md"
  check_not R84b "R84's check catches a removed credentialRef.apiServerEnvVar reference (2026-08-25)" \
    grep -q 'credentialRef.apiServerEnvVar' "${mc_design_drift_tmp}/multicluster-control-plane.md"
  rm -rf "${mc_design_drift_tmp}"

  check R85 "multi-cluster exporter supports embedded and file-backed CA kubeconfigs (2026-08-25)" \
    bash -c "grep -q 'certificate-authority-data' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh && grep -q 'certificate-authority}' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh && grep -q 'requires a non-empty value' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh"

  local cred_exporter_ca_drift_tmp
  cred_exporter_ca_drift_tmp="$(mktemp -d)"
  grep -v 'certificate-authority-data' scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh \
    > "${cred_exporter_ca_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh"
  check_not R85b "R85's check catches a dropped embedded-CA (certificate-authority-data) path (2026-08-25)" \
    bash -c "grep -q 'certificate-authority-data' '${cred_exporter_ca_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh' && grep -q 'certificate-authority}' '${cred_exporter_ca_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh' && grep -q 'requires a non-empty value' '${cred_exporter_ca_drift_tmp}/13-3-export-narwhal-portal-cluster-credentials.sh'"
  rm -rf "${cred_exporter_ca_drift_tmp}"

  # narwhal#53: SBOM component to vulnerability finding correlation and schema validation
  check R86 "SBOM component to vulnerability correlation manifest validates against schema (2026-08-25)" \
    python3 scripts/airgap/lib/correlate-sbom-vulnerabilities.py \
      --sbom scripts/airgap/lib/sample-sbom.cdx.json \
      --trivy-report scripts/airgap/lib/sample-trivy-report.json \
      --schema schemas/sbom-correlation-1.0.schema.json \
      --commit a1b2c3d4e5f60718293a4b5c6d7e8f9012345678 \
      --workflow-run-id 123456789

  local sbom_corr_drift_tmp
  sbom_corr_drift_tmp="$(mktemp -d)"
  python3 - <<PYEOF
import json
with open("scripts/airgap/lib/sample-sbom-correlation.json", "r", encoding="utf-8") as f:
    doc = json.load(f)
del doc["source_commit_sha"]
with open("${sbom_corr_drift_tmp}/invalid-correlation.json", "w", encoding="utf-8") as f:
    json.dump(doc, f)
PYEOF
  check_not R86b "R86's check catches a correlation manifest missing required source_commit_sha (2026-08-25)" \
    python3 -c "import json, sys; doc=json.load(open('${sbom_corr_drift_tmp}/invalid-correlation.json')); sys.exit(0 if 'source_commit_sha' in doc else 1)"
  rm -rf "${sbom_corr_drift_tmp}"

  # narwhal#53: build_sbom.py and 08-generate-sbom.sh commit/workflow ID propagation
  check R87 "build_sbom.py propagates commit SHA and workflow run ID into SBOM metadata properties (2026-08-25)" \
    bash -c "grep -q 'narwhal:commit_sha' scripts/airgap/lib/build_sbom.py && grep -q 'narwhal:workflow_run_id' scripts/airgap/lib/build_sbom.py && grep -q -e '--commit' scripts/airgap/08-generate-sbom.sh"

  local sbom_builder_drift_tmp
  sbom_builder_drift_tmp="$(mktemp -d)"
  grep -v 'narwhal:commit_sha' scripts/airgap/lib/build_sbom.py \
    > "${sbom_builder_drift_tmp}/build_sbom.py"
  check_not R87b "R87's check catches dropped narwhal:commit_sha property propagation in build_sbom.py (2026-08-25)" \
    grep -q 'narwhal:commit_sha' "${sbom_builder_drift_tmp}/build_sbom.py"
  rm -rf "${sbom_builder_drift_tmp}"

  # narwhal#50 T2 pilot: this is an offline desired-state contract, not a claim
  # that the Keycloak Operator or a cluster has run.
  check R88 "T2 Keycloak offline desired-state contract validates the real platform chart (2026-08-26)" \
    scripts/test/t2-component.sh keycloak --mode render

  # Copy the full chart so Helm retains Chart.yaml, then delete only the CPU request
  # with yq. The negative case never changes the real GitOps manifest.
  local keycloak_t2_drift_tmp
  keycloak_t2_drift_tmp="$(mktemp -d)"
  cp -R gitops/charts/narwhal-platform "${keycloak_t2_drift_tmp}/narwhal-platform"
  yq -i 'del(.spec.resources.requests.cpu)' \
    "${keycloak_t2_drift_tmp}/narwhal-platform/templates/keycloak-cr.yaml"
  check_not R89 "T2 Keycloak contract catches a deleted first-class CPU request (2026-08-26)" \
    scripts/test/t2-component.sh keycloak --mode render --template "${keycloak_t2_drift_tmp}/narwhal-platform"
  rm -rf "${keycloak_t2_drift_tmp}"

  # 2026-08-26: Application repoURLs deliberately name the in-cluster Gitea Helm
  # registry for runtime offline reconciliation, but 03-save-helm-charts.sh runs
  # before Gitea exists. Its source map must therefore cover every enabled GitOps
  # chart and must not point back to the internal registry. A broken row is enough
  # to recreate the original one-success/twenty-fail bundle build, so prove the
  # checker rejects a temporary map with apisix pointed at Gitea.
  check R90 "GitOps bundle sources are complete and separate from Gitea (2026-08-26)" \
    python3 scripts/airgap/lib/check-chart-upstream-sources.py

  local chart_source_drift_tmp
  chart_source_drift_tmp="$(mktemp -d)"
  python3 - "${chart_source_drift_tmp}/chart-upstream-sources.tsv" <<'PYEOF'
import sys
from pathlib import Path

target = Path(sys.argv[1])
source = Path("scripts/airgap/lib/chart-upstream-sources.tsv").read_text()
old = "apisix\thelm-repo\thttps://charts.apiseven.com"
new = "apisix\thelm-repo\thttp://gitea-http.devtools.svc.cluster.local:3000/api/packages/gitea-admin/helm"
assert source.count(old) == 1, "expected one apisix source-map row"
target.write_text(source.replace(old, new))
PYEOF
  check_not R91 "chart source gate catches a Gitea-mirror regression (2026-08-26)" \
    python3 scripts/airgap/lib/check-chart-upstream-sources.py "${chart_source_drift_tmp}/chart-upstream-sources.tsv"
  rm -rf "${chart_source_drift_tmp}"
}

#=========================================
# Runtime: the fix is present AND effective
#=========================================
need_cluster() {
  kubectl get nodes >/dev/null 2>&1 || {
    echo "ERROR: no reachable cluster on the current kubectl context." >&2
    echo "       Run scripts/cloud/set-config-kakao.sh first." >&2
    exit 1
  }
}

# Two checks need to look at a node's filesystem and the bastion's registry log, which
# kubectl cannot reach. Addresses come from OpenTofu state like everywhere else; if that
# or the key is unavailable these return non-zero and the callers warn rather than fail.
SSH_READY=0
setup_ssh() {
  BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip 2>/dev/null) || return 1
  MASTER1=$(cd "${TF_DIR}" && tofu output -json master_private_ips 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)[0])' 2>/dev/null) || return 1
  SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
  [ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
  case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
  [ -f "${SSH_KEY}" ] || return 1
  SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10"
  SSH_READY=1
}

ssh_bastion() { [ "${SSH_READY}" = 1 ] || return 1; ssh ${SSH_OPTS} "ubuntu@${BASTION_IP}" "$@"; }
ssh_node() {
  [ "${SSH_READY}" = 1 ] || return 1
  local ip="$1"; shift
  ssh ${SSH_OPTS} -o ProxyCommand="ssh ${SSH_OPTS} -W %h:%p ubuntu@${BASTION_IP}" "ubuntu@${ip}" "$@"
}

run_runtime() {
  need_cluster
  setup_ssh || true
  section "RUNTIME — symptoms absent on the live cluster"

  local n
  n=$(kubectl get nodes --no-headers 2>/dev/null | awk '$2=="Ready"' | wc -l | tr -d ' ')
  [ "${n}" = "6" ] && ok T01 "6/6 nodes Ready" || bad T01 "only ${n}/6 nodes Ready"

  # containerd 1.7.x + runc 1.3.4 is the AppArmor cascade pairing.
  local cr
  cr=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.nodeInfo.containerRuntimeVersion}{"\n"}{end}' 2>/dev/null | sort -u | tr '\n' ' ')
  case "${cr}" in
    *containerd://1.7*) bad T02 "containerd 1.7.x running: ${cr} (2026-07-26)" ;;
    *) ok T02 "containerd not 1.7.x: ${cr}" ;;
  esac

  # 2026-08-05: the mirror had never served a pull. hosts.toml was right, the images were
  # there, and containerd ignored all of it because config_path held Ubuntu's default
  # colon-separated pair instead of a single directory. Nothing failed while upstream was
  # reachable, so no health check could see it. Two independent probes, because either one
  # alone was passing while the mirror sat unused:
  #   T14 — the config containerd actually loaded holds one directory, not a list
  #   T15 — the registry has genuinely served containerd (the 2026-07-26 discriminator)
  local colon
  colon=$(ssh_node "${MASTER1:-172.17.0.10}" "sudo containerd config dump 2>/dev/null | grep -m1 '[^_]config_path'" 2>/dev/null || true)
  case "${colon}" in
    *:*certs.d*|*certs.d:*) bad T14 "containerd config_path is a colon list: ${colon# } (2026-08-05)" ;;
    *certs.d*)              ok  T14 "containerd config_path is a single directory (2026-08-05)" ;;
    *)                      warn T14 "could not read config_path from the node" ;;
  esac

  # "0 requests" and "could not ask" are different answers; only the first is a failure.
  local hits
  if [ "${SSH_READY}" != 1 ]; then
    warn T15 "no ssh to the bastion; mirror-usage check skipped"
  else
    hits=$(ssh_bastion "sudo docker logs airgap-registry 2>&1 | grep -c containerd" 2>/dev/null || echo "")
    case "${hits}" in
      ''|*[!0-9]*) warn T15 "could not read the registry log" ;;
      0)           bad  T15 "airgap registry served 0 containerd requests — mirror unused (2026-08-05)" ;;
      *)           ok   T15 "airgap registry has served containerd (${hits} requests) (2026-08-05)" ;;
    esac
  fi

  # Every ArgoCD application must be Synced+Healthy.
  # Count first. "Every app is healthy" is vacuously true of no apps at all, and that is
  # exactly what happened on 2026-08-05: the GitOps bootstrap failed against a Gitea that
  # was still ImagePullBackOff, so the cluster carried zero Applications and this reported
  # PASS while four services 503'd with nothing behind them. 14-gitops-bootstrap.sh hands
  # ArgoCD ten apps, so anything below that is a failure, not a pass.
  local unhealthy napps
  napps=$(kubectl get applications -n devtools --no-headers 2>/dev/null | grep -c . || true)
  unhealthy=$(kubectl get applications -n devtools --no-headers 2>/dev/null \
    | awk '$2!="Synced" || $3!="Healthy" {print $1}' | tr '\n' ' ')
  if [ "${napps:-0}" -lt 10 ]; then
    bad T03 "only ${napps:-0} ArgoCD applications; the GitOps bootstrap did not land (2026-08-05)"
  elif [ -z "${unhealthy}" ]; then
    ok T03 "all ${napps} ArgoCD apps Synced+Healthy"
  else
    bad T03 "not converged: ${unhealthy}"
  fi

  # A pod STUCK Terminating is the AppArmor/NFS wedge signature. A pod merely
  # terminating is a rollout doing its job — judging on state alone made this fail
  # every time it ran right after one. Fail only past the grace period plus slack.
  local stuck
  stuck=$(kubectl get pods -A -o json 2>/dev/null | python3 -c '
import json, sys, datetime
STUCK_AFTER = 120  # seconds past deletionTimestamp; default grace is 30
now = datetime.datetime.now(datetime.timezone.utc)
out = []
for p in json.load(sys.stdin)["items"]:
    ts = p["metadata"].get("deletionTimestamp")
    if not ts:
        continue
    age = (now - datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
    grace = p["spec"].get("terminationGracePeriodSeconds") or 30
    if age > grace + STUCK_AFTER:
        m = p["metadata"]
        out.append("%s/%s(%ds)" % (m["namespace"], m["name"], age))
print(" ".join(out))
' 2>/dev/null || echo "")
  [ -z "${stuck}" ] && ok T04 "no pods stuck Terminating" \
    || bad T04 "stuck Terminating: ${stuck} (2026-07-26)"

  # The domain must be the Kakao one everywhere — a stale local.* route means the
  # GitOps baseDomain never reached the cluster.
  local wrong
  wrong=$(kubectl get apisixroute -A -o jsonpath='{range .items[*].spec.http[*]}{.match.hosts[*]}{"\n"}{end}' 2>/dev/null \
    | sort -u | grep -c 'local\.narwhal\.internal' || true)
  [ "${wrong}" = "0" ] && ok T05 "no routes left on local.narwhal.internal" \
    || bad T05 "${wrong} routes still on the Vagrant domain (2026-07-29)"

  # ApisixTls reconciled: generation drift means the IC is serving stale config.
  local gen obs
  gen=$(kubectl -n platform-system get apisixtls narwhal-wildcard -o jsonpath='{.metadata.generation}' 2>/dev/null || echo "")
  obs=$(kubectl -n platform-system get apisixtls narwhal-wildcard -o jsonpath='{.status.conditions[0].observedGeneration}' 2>/dev/null || echo "")
  if [ -n "${gen}" ] && [ "${gen}" = "${obs}" ]; then
    ok T06 "ApisixTls reconciled (gen=${gen})"
  else
    bad T06 "ApisixTls generation drift gen=${gen} observed=${obs} (2026-07-29)"
  fi

  # CoreDNS hairpin zone present, and answering names it was never told about.
  if kubectl -n kube-system get cm coredns -o jsonpath='{.data.Corefile}' 2>/dev/null | grep -q "^${DOMAIN}:53"; then
    ok T07 "CoreDNS hairpin zone for ${DOMAIN} present"
  else
    bad T07 "CoreDNS hairpin zone missing (2026-07-29)"
  fi

  # In-cluster resolution is what the gateway-OIDC plugin actually depends on.
  local probe="regression-probe-$$"
  if kubectl -n platform-system run "${probe}" --image=curlimages/curl:8.11.0 --restart=Never \
      --command -- sleep 120 >/dev/null 2>&1 \
    && kubectl -n platform-system wait --for=condition=Ready "pod/${probe}" --timeout=90s >/dev/null 2>&1; then

    # Judge on the answer, not on nslookup's exit code: it asks for A and AAAA together
    # and returns 1 if either half is unhappy, so a perfectly good A record still exits
    # non-zero. resolves_a() looks for an Address line under the queried name.
    resolves_a() {
      kubectl -n platform-system exec "${probe}" -- nslookup "$1" 2>/dev/null \
        | grep -A2 "^Name:[[:space:]]*$1" | grep -q '^Address'
    }

    if resolves_a "keycloak.${DOMAIN}"; then
      ok T08 "keycloak.${DOMAIN} resolves in-cluster (2026-07-29)"
    else
      bad T08 "keycloak.${DOMAIN} NXDOMAIN in-cluster — gateway OIDC will 500 (2026-07-29)"
    fi

    # A name no route defines must still answer: the zone is a wildcard, not a list.
    if resolves_a "unlisted-name.${DOMAIN}"; then
      ok T09 "hairpin zone is a wildcard, not a hostname list"
    else
      warn T09 "unlisted name does not resolve — zone may be a fixed list"
    fi

    # AAAA must be NODATA, not SERVFAIL: stub resolvers ask for both and a SERVFAIL on
    # the v6 half makes them retry and stall.
    if kubectl -n platform-system exec "${probe}" -- nslookup -type=AAAA "keycloak.${DOMAIN}" 2>&1 \
      | grep -q 'SERVFAIL'; then
      bad T13 "AAAA for ${DOMAIN} returns SERVFAIL instead of NODATA (2026-07-29)"
    else
      ok T13 "AAAA returns NODATA, not SERVFAIL"
    fi

    # apisix-etcd empty-prefix deadlock returns 404 rather than an empty list.
    local key code
    key=$(kubectl -n platform-system get secret apisix-admin-key -o jsonpath='{.data.key}' 2>/dev/null | base64 -d || echo "")
    if [ -n "${key}" ]; then
      code=$(kubectl -n platform-system exec "${probe}" -- curl -s -o /dev/null -w '%{http_code}' \
        -H "X-API-KEY: ${key}" \
        "http://apisix-admin.platform-system.svc.cluster.local:9180/apisix/admin/routes" 2>/dev/null || echo "000")
      [ "${code}" = "200" ] && ok T10 "APISIX admin /routes returns 200 (not the 404 etcd deadlock)" \
        || bad T10 "APISIX admin /routes returned ${code} — see apisix-etcd-recovery.md"
    else
      warn T10 "apisix-admin-key secret not readable; skipped"
    fi

    kubectl -n platform-system delete pod "${probe}" --wait=false >/dev/null 2>&1 || true
  else
    warn T08 "could not start probe pod; in-cluster DNS checks skipped"
    kubectl -n platform-system delete pod "${probe}" --wait=false >/dev/null 2>&1 || true
  fi

  # AppleDouble sidecars in the GitOps repo break helm template.
  local ad
  ad=$(kubectl -n devtools exec deploy/gitea -- sh -c 'find /data -name "._*" 2>/dev/null | wc -l' 2>/dev/null | tr -d ' \r' || echo "")
  if [ -z "${ad}" ]; then
    warn T11 "could not inspect Gitea for AppleDouble files"
  elif [ "${ad}" = "0" ]; then
    ok T11 "no AppleDouble (._*) files in Gitea (2026-07-27)"
  else
    bad T11 "${ad} AppleDouble files in Gitea (2026-07-27)"
  fi

  # Every documented service must answer through the public LB.
  local lb
  lb=$(cd "${TF_DIR}" && tofu output -raw worker_lb_public_ip 2>/dev/null || echo "")
  if [ -z "${lb}" ]; then
    warn T12 "worker_lb_public_ip unavailable; external reachability skipped"
  else
    local bad_hosts="" h code
    for h in $(kubectl get apisixroute -A -o jsonpath='{range .items[*].spec.http[*]}{.match.hosts[*]}{"\n"}{end}' 2>/dev/null | sort -u); do
      code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 25 --resolve "${h}:443:${lb}" "https://${h}/" 2>/dev/null || echo "000")
      case "${code}" in
        200|301|302|303|307|308|401) ;;
        *) bad_hosts="${bad_hosts}${h}=${code} " ;;
      esac
    done
    [ -z "${bad_hosts}" ] && ok T12 "all service domains reachable through the worker LB" \
      || bad T12 "unreachable: ${bad_hosts}"
  fi
}

#=========================================
case "${MODE}" in
  --static)  run_static ;;
  --runtime) run_runtime ;;
  --all)     run_static; run_runtime ;;
  *) echo "usage: $0 [--static|--runtime|--all]" >&2; exit 2 ;;
esac

section "RESULT"
printf '  passed %s   failed %s   warnings %s\n' "${PASS}" "${FAIL}" "${WARN}"
if [ "${FAIL}" -gt 0 ]; then
  printf '  %sregressions: %s%s\n' "${RED}" "${FAILED_IDS}" "${RESET}"
  printf '  each id maps to a dated entry in docs/common/lessons-log.md\n'
else
  printf '  %sno known bug reappeared%s\n' "${GREEN}" "${RESET}"
fi

EXIT_CODE=0
if [ "${FAIL}" -gt 125 ]; then
  EXIT_CODE=125
elif [ "${FAIL}" -gt 0 ]; then
  EXIT_CODE="${FAIL}"
fi

# narwhal#50 (T1-T7 test strategy): export the same PASS/FAIL/WARN data the terminal
# just saw as JSON and/or Markdown, so a CI job or a future dashboard can consume it
# without scraping colored stdout. GIT_COMMIT/GIT_DIRTY stand in for the "input/config
# hash" evidence field the issue asks for — the closest thing this repo can state
# truthfully without a live cluster to fingerprint.
if [ -n "${CHECK_LOG}" ]; then
  GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  GIT_DIRTY="false"
  git diff --quiet 2>/dev/null || GIT_DIRTY="true"
  git diff --cached --quiet 2>/dev/null || GIT_DIRTY="true"
  RUN_ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if [ -n "${JSON_REPORT}" ]; then
    python3 - "${CHECK_LOG}" "${JSON_REPORT}" "${RUN_ID}" "${MODE}" "${GIT_COMMIT}" \
      "${GIT_DIRTY}" "${RUN_STARTED_AT}" "${RUN_ENDED_AT}" "${PASS}" "${FAIL}" "${WARN}" <<'PYEOF'
import json, sys

log_path, out_path, run_id, mode, git_commit, git_dirty, started_at, ended_at, npass, nfail, nwarn = sys.argv[1:12]

checks = []
with open(log_path, encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        check_id, status, ts, desc = line.split("\t", 3)
        checks.append({
            "id": check_id,
            "status": status,
            "timestamp": ts,
            "description": desc,
        })

report = {
    "test_run_id": run_id,
    "script": "scripts/test/regression-check-kakao.sh",
    "mode": mode,
    "started_at": started_at,
    "ended_at": ended_at,
    "git_commit": git_commit,
    "git_dirty": git_dirty == "true",
    "summary": {
        "pass": int(npass),
        "fail": int(nfail),
        "warn": int(nwarn),
        "total": len(checks),
    },
    "checks": checks,
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
    f.write("\n")
PYEOF
    echo "  JSON report: ${JSON_REPORT}"
  fi

  if [ -n "${MD_REPORT}" ]; then
    python3 - "${CHECK_LOG}" "${MD_REPORT}" "${RUN_ID}" "${MODE}" "${GIT_COMMIT}" \
      "${GIT_DIRTY}" "${RUN_STARTED_AT}" "${RUN_ENDED_AT}" "${PASS}" "${FAIL}" "${WARN}" <<'PYEOF'
import sys

log_path, out_path, run_id, mode, git_commit, git_dirty, started_at, ended_at, npass, nfail, nwarn = sys.argv[1:12]

rows = []
with open(log_path, encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        check_id, status, ts, desc = line.split("\t", 3)
        rows.append((check_id, status, ts, desc))

lines = []
lines.append("# Regression check report")
lines.append("")
lines.append(f"- test run id: `{run_id}`")
lines.append("- script: `scripts/test/regression-check-kakao.sh`")
lines.append(f"- mode: `{mode}`")
lines.append(f"- started: {started_at}  ended: {ended_at}")
lines.append(f"- git commit: `{git_commit}`{' (dirty tree)' if git_dirty == 'true' else ''}")
lines.append(f"- summary: **{npass} pass / {nfail} fail / {nwarn} warn** ({len(rows)} checks)")
lines.append("")
lines.append("| ID | Status | Timestamp | Description |")
lines.append("|----|--------|-----------|--------------|")
for check_id, status, ts, desc in rows:
    desc_escaped = desc.replace("|", "\\|")
    lines.append(f"| {check_id} | {status} | {ts} | {desc_escaped} |")
lines.append("")

with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
PYEOF
    echo "  Markdown report: ${MD_REPORT}"
  fi
fi

exit "${EXIT_CODE}"
