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
# Usage:
#   scripts/test/regression-check-kakao.sh --static
#   scripts/test/regression-check-kakao.sh --runtime
#   DOMAIN=kakao.narwhal.internal scripts/test/regression-check-kakao.sh

MODE="${1:---all}"
DOMAIN="${DOMAIN:-kakao.narwhal.internal}"
TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"

cd "$(dirname "$0")/../.."

if [ -t 1 ]; then
  BOLD=$'\e[1m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RESET=$'\e[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

PASS=0; FAIL=0; WARN=0
FAILED_IDS=""

# ok <id> <description> — the check passed
ok()   { PASS=$((PASS + 1)); printf '  %sPASS%s  %-14s %s\n' "${GREEN}" "${RESET}" "$1" "$2"; }
bad()  { FAIL=$((FAIL + 1)); FAILED_IDS="${FAILED_IDS}$1 "; printf '  %sFAIL%s  %-14s %s\n' "${RED}" "${RESET}" "$1" "$2"; }
warn() { WARN=$((WARN + 1)); printf '  %sWARN%s  %-14s %s\n' "${YELLOW}" "${RESET}" "$1" "$2"; }

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

  check_not R29 "no internet fetches in the default install path (2026-08-04)" \
    grep -rqE '^[^#]*(curl|wget|kubectl apply -f)[^#]*https://(get\.helm\.sh|raw\.githubusercontent\.com|github\.com/(cilium|kubernetes-sigs|argoproj|keycloak|mikefarah|dasomel))' \
      scripts/cluster/03-cni-install.sh scripts/cluster/04-addons.sh \
      scripts/cluster/05-nfs-quota-agent.sh scripts/cluster/11-keycloak.sh \
      scripts/cluster/13-argocd.sh scripts/common/01-prerequisites.sh

  # bastion.tf must be tracked — a blanket csp/ gitignore once hid it from fresh clones.
  check R18 "bastion.tf is tracked by git" \
    git ls-files --error-unmatch "${TF_DIR}/bastion.tf"

  # "The bundle is still incomplete" has been the finding three separate times, each
  # from a different cause (live-only image list, docker-archive refusing to overwrite,
  # bare refs). Check the shape rather than any one of those: the three counts must
  # agree, and the bootstrap tar must not predate the images around it.
  local arch bundle
  arch=$(uname -m); [ "${arch}" = "x86_64" ] && arch=amd64
  [ "${arch}" = "aarch64" ] && arch=arm64
  bundle="${AIRGAP_BUNDLE_DIR:-narwhal-airgap-bundle-${arch}}"
  if [ ! -d "${bundle}/oci" ]; then
    warn R19 "no bundle at ${bundle}; airgap checks skipped"
  else
    local want have layouts stale
    want=$(grep -cvE '^\s*(#|$)' scripts/airgap/images.txt)
    have=$(grep -cvE '^\s*$' "${bundle}/manifest.txt")
    layouts=$(find "${bundle}/oci" -maxdepth 4 -name index.json | wc -l | tr -d ' ')
    if [ "${want}" = "${have}" ] && [ "${want}" = "${layouts}" ]; then
      ok R19 "bundle complete: ${want} images in list, manifest and oci"
    else
      bad R19 "bundle counts disagree — list=${want} manifest=${have} oci=${layouts}"
    fi

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
        || incomplete="${incomplete}${a} "
    done
    if [ -z "${incomplete}" ]; then
      ok R30 "both bundles carry binaries and manifests (2026-08-05)"
    else
      warn R30 "bundle missing bin/manifests: ${incomplete}(run 07-save-binaries.sh)"
    fi

    if [ -f "${bundle}/bootstrap/registry.tar" ]; then
      stale=$(find "${bundle}/oci" -name index.json -newer "${bundle}/bootstrap/registry.tar" | wc -l | tr -d ' ')
      [ "${stale}" = "0" ] && ok R20 "bootstrap registry.tar not stale (2026-07-28)" \
        || bad R20 "registry.tar older than ${stale} image layouts (2026-07-28)"
    else
      bad R20 "bundle has no bootstrap/registry.tar"
    fi
  fi
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

run_runtime() {
  need_cluster
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

  # Every ArgoCD application must be Synced+Healthy.
  local unhealthy
  unhealthy=$(kubectl get applications -n devtools --no-headers 2>/dev/null \
    | awk '$2!="Synced" || $3!="Healthy" {print $1}' | tr '\n' ' ')
  [ -z "${unhealthy}" ] && ok T03 "all ArgoCD apps Synced+Healthy" \
    || bad T03 "not converged: ${unhealthy}"

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
  [ "${FAIL}" -gt 125 ] && exit 125
  exit "${FAIL}"
fi
printf '  %sno known bug reappeared%s\n' "${GREEN}" "${RESET}"
