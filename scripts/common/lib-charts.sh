#!/bin/bash
# Resolve Helm charts from the airgap bundle instead of a public repository.
#
# The bundle has carried 24 chart tarballs since 03-save-helm-charts.sh was written, and
# nothing ever read them: every script did `helm repo add <public url>` and installed from
# there. So "airgap" only ever covered container images — an install still needed the
# internet for every chart, and the squid proxy hid that. It surfaced as flakiness (a
# chart fetch timing out mid-run) rather than as the hard failure it would be on a truly
# isolated network.
#
# Installing from the tarball also pins the version. None of the `helm upgrade --install`
# calls passed --version, so each one took whatever the repo happened to be serving that
# day; the bundle's filename freezes it.
#
# Usage:
#   source /home/vagrant/scripts/common/lib-charts.sh
#   helm upgrade --install cnpg "$(chart cloudnative-pg)" -n database ...

# Where the staged charts live. stage-kakao-nodes.sh puts the bundle's charts/ here;
# NARWHAL_CHART_DIR overrides it for anyone laying them out differently.
NARWHAL_CHART_DIR="${NARWHAL_CHART_DIR:-/home/vagrant/charts}"

# chart <name> — print the path of the bundled tarball for <name>, or fail loudly.
#
# The version suffix has to be matched, not just prefixed: a plain `${name}-*.tgz` glob
# answers `apisix` with apisix-ingress-controller-0.14.1.tgz, which installs the wrong
# chart under the right release name and is a genuinely awful thing to debug.
chart() {
  local name="$1" f match=""
  for f in "${NARWHAL_CHART_DIR}/${name}"-*.tgz; do
    [ -e "$f" ] || continue
    case "$(basename "$f")" in
      "${name}"-[0-9]*.tgz|"${name}"-v[0-9]*.tgz) match="$f"; break ;;
    esac
  done

  if [ -z "${match}" ]; then
    echo "ERROR: chart '${name}' is not in ${NARWHAL_CHART_DIR}" >&2
    echo "       This install does not fetch charts from the internet — the airgap" >&2
    echo "       bundle must supply it. Re-run scripts/airgap/03-save-helm-charts.sh" >&2
    echo "       and re-stage, or check the chart name." >&2
    if [ -d "${NARWHAL_CHART_DIR}" ]; then
      echo "       Available: $(ls "${NARWHAL_CHART_DIR}" 2>/dev/null | tr '\n' ' ')" >&2
    else
      echo "       ${NARWHAL_CHART_DIR} does not exist — charts were never staged." >&2
    fi
    return 1
  fi

  printf '%s' "${match}"
}
