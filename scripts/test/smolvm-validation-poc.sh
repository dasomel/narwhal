#!/bin/bash
set -euo pipefail

# D1: Limit this prototype to the verified Apple Silicon host. This narrows host coverage;
# add a platform only with its own tested smolvm binary and platform-specific image digests.
# D2: Fetch digest-pinned OCI archives before VM start, then keep the guest offline. This
# requires skopeo and local disk; never enable guest networking to pull validation images.
# D3: Place VM inputs under /tmp because macOS TMPDIR paths under /var/folders fail in
# libkrun's host-file access path. This uses the shared system temp area; keep contents ephemeral.

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
smolvm_bin="${SMOLVM_BIN:-smolvm}"
expected_runtime="smolvm 1.18.2"
helm_digest="sha256:57b8041193d7b278878eec07a21565c159f4daaa713db75e9bdfa8c0d83a34e7"
kubeconform_digest="sha256:cb8ca5a6e9bfb60b1858d65cba57c27f8080befcd5282b21518b860192b91e31"
helm_archive_sha="306ca27f5c7f6ff0cc5af65c517745e14c0f879d272931855a325e3c3ab9f6d0"
kubeconform_archive_sha="b2f9b9ed0e484c7607be958c4ff45232dfe5573005980fca623a74412e1fe855"
cr_group_filter='select(.apiVersion == "argoproj.io/v1alpha1" or .apiVersion == "postgresql.cnpg.io/v1" or .apiVersion == "monitoring.coreos.com/v1" or .apiVersion == "monitoring.coreos.com/v1alpha1" or .apiVersion == "kyverno.io/v1" or .apiVersion == "aquasecurity.github.io/v1alpha1" or .apiVersion == "metallb.io/v1beta1")'

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "PoC is verified only on Apple Silicon macOS"
if [[ "$smolvm_bin" == */* ]]; then
  [[ -x "$smolvm_bin" ]] || fail "SMOLVM_BIN is not executable: $smolvm_bin"
else
  command -v "$smolvm_bin" >/dev/null 2>&1 || fail "smolvm is required; set SMOLVM_BIN to its verified wrapper"
fi
[[ "$("$smolvm_bin" --version)" == "$expected_runtime" ]] || fail "expected $expected_runtime"

for dependency in skopeo yq shasum python3; do
  command -v "$dependency" >/dev/null 2>&1 || fail "missing required command: $dependency"
done
[[ "$(skopeo --version)" == "skopeo version 1.24.1" ]] || fail "expected skopeo version 1.24.1"
[[ "$(yq --version)" == "yq (https://github.com/mikefarah/yq/) version v4.53.6" ]] || fail "expected yq v4.53.6"

evidence_dir="${SMOLVM_EVIDENCE_DIR:-${TMPDIR:-/tmp}/narwhal-smolvm-evidence-$(date -u '+%Y%m%dT%H%M%SZ')-$$}"
mkdir -p "$evidence_dir"
[[ -z "$(find "$evidence_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "evidence directory must be empty: $evidence_dir"

workdir="$(mktemp -d "/tmp/narwhal-smolvm-poc.XXXXXX")"
cleanup() { rm -rf "$workdir"; }
trap cleanup EXIT
mkdir -p "$workdir/home/.cache" "$workdir/chart" "$workdir/custom-resources" "$workdir/schemas"
printf '{}\n' > "$workdir/registry-auth.json"

# docker.io/alpine/helm is upstream-maintained and provides the Helm CLI; its selected
# Linux/arm64 manifest and the Kubeconform manifest below are both pinned by digest.
skopeo copy --authfile "$workdir/registry-auth.json" --override-arch arm64 --override-os linux \
  "docker://docker.io/alpine/helm@$helm_digest" "docker-archive:$workdir/helm.tar:narwhal-helm:pinned"
skopeo copy --authfile "$workdir/registry-auth.json" --override-arch arm64 --override-os linux \
  "docker://ghcr.io/yannh/kubeconform@$kubeconform_digest" "docker-archive:$workdir/kubeconform.tar:narwhal-kubeconform:pinned"
[[ "$(shasum -a 256 "$workdir/helm.tar" | awk '{print $1}')" == "$helm_archive_sha" ]] || fail "Helm archive checksum mismatch"
[[ "$(shasum -a 256 "$workdir/kubeconform.tar" | awk '{print $1}')" == "$kubeconform_archive_sha" ]] || fail "Kubeconform archive checksum mismatch"

cp -R "$repo_root/gitops/charts/narwhal-apps/." "$workdir/chart/"
yq eval-all "$cr_group_filter" "$repo_root"/gitops/resources/*.yaml > "$workdir/custom-resources/custom-resources.yaml"
cp -R "$repo_root/schemas/crds/." "$workdir/schemas/"

tree_digest() {
  python3 - "$1" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    relative = path.relative_to(root).as_posix().encode()
    content = path.read_bytes()
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
print(digest.hexdigest())
PY
}
chart_sha="$(tree_digest "$workdir/chart")"
resources_sha="$(tree_digest "$workdir/custom-resources")"
schemas_sha="$(tree_digest "$workdir/schemas")"

smolvm_home="$workdir/home"
run_guest() {
  local name="$1" image="$2"
  shift 2
  local start_epoch end_epoch
  RUN_STARTED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  start_epoch="$(date +%s)"
  if env -i PATH="$PATH" HOME="$smolvm_home" XDG_CACHE_HOME="$smolvm_home/.cache" \
    "$smolvm_bin" machine run --cpus 2 --mem 2048 --storage 20 --timeout 60s --image "$image" "$@" \
    > "$evidence_dir/$name.stdout" 2> "$evidence_dir/$name.stderr"; then
    RUN_STATUS=0
  else
    RUN_STATUS=$?
  fi
  RUN_ENDED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  end_epoch="$(date +%s)"
  RUN_DURATION=$((end_epoch - start_epoch))
  RUN_VM="$(sed -nE 's/.*(vm-[[:xdigit:]]+).*/\1/p' "$evidence_dir/$name.stdout" "$evidence_dir/$name.stderr" | head -1 || true)"
  cat "$evidence_dir/$name.stdout"
  cat "$evidence_dir/$name.stderr" >&2
}

# shellcheck disable=SC2016 # This command runs in the guest; expand guest variables there.
run_guest helm "$workdir/helm.tar" \
  --volume "$workdir/chart:/workspace:ro" -- \
  sh -c 'set -eu
    helm lint /workspace
    helm template narwhal-apps /workspace >/tmp/rendered.yaml
    echo HELM_RENDER_OK
    if busybox wget -T 4 -qO- https://example.com >/dev/null 2>&1; then echo NETWORK_PROBE_UNEXPECTEDLY_SUCCEEDED; exit 21; else echo NETWORK_BLOCKED; fi
    if touch /workspace/.write-probe 2>/dev/null; then echo READ_ONLY_PROBE_UNEXPECTEDLY_SUCCEEDED; exit 22; else echo MOUNT_WRITE_BLOCKED; fi
    if test -e /root/.kube/config || test -e /var/run/secrets/kubernetes.io/serviceaccount/token || test -n "${KUBECONFIG:-}"; then echo KUBERNETES_CREDENTIAL_PROBE_UNEXPECTEDLY_SUCCEEDED; exit 23; else echo KUBERNETES_CREDENTIALS_ABSENT; fi
    if env | cut -d= -f1 | grep -Ei "(^|_)(TOKEN|PASSWORD|SECRET|CREDENTIAL)(_|$)" >/dev/null; then echo CREDENTIAL_ENV_PROBE_UNEXPECTEDLY_SUCCEEDED; exit 24; else echo CREDENTIAL_ENV_ABSENT; fi
    if command -v kubectl >/dev/null 2>&1; then echo KUBECTL_PRESENT; exit 25; else echo KUBECTL_ABSENT; fi
    printf "rendered_manifest_bytes="; wc -c </tmp/rendered.yaml'
helm_status="$RUN_STATUS"
for marker in HELM_RENDER_OK NETWORK_BLOCKED MOUNT_WRITE_BLOCKED KUBERNETES_CREDENTIALS_ABSENT CREDENTIAL_ENV_ABSENT KUBECTL_ABSENT; do
  grep -q "$marker" "$evidence_dir/helm.stdout" || helm_status=1
done
[[ -n "$RUN_VM" ]] || helm_status=1
if grep -q 'failed to pre-format disk' "$evidence_dir/helm.stderr"; then
  helm_status=1
fi
printf 'helm: command=helm-lint-and-template runtime=%s vm=%s started=%s ended=%s duration_seconds=%s exit=%s image=%s\n' \
  "$expected_runtime" "${RUN_VM:-unknown}" "$RUN_STARTED" "$RUN_ENDED" "$RUN_DURATION" "$helm_status" "$helm_digest" >> "$evidence_dir/evidence.txt"

run_guest kubeconform "$workdir/kubeconform.tar" \
  --volume "$workdir/custom-resources:/workspace:ro" \
  --volume "$workdir/schemas:/schemas:ro" -- \
  /kubeconform -strict -summary \
  -schema-location '/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  /workspace/custom-resources.yaml
kubeconform_status="$RUN_STATUS"
grep -q 'Valid: 24, Invalid: 0, Errors: 0, Skipped: 0' "$evidence_dir/kubeconform.stdout" || kubeconform_status=1
[[ -n "$RUN_VM" ]] || kubeconform_status=1
if grep -q 'failed to pre-format disk' "$evidence_dir/kubeconform.stderr"; then
  kubeconform_status=1
fi
printf 'kubeconform: command=kubeconform-strict runtime=%s vm=%s started=%s ended=%s duration_seconds=%s exit=%s image=%s\n' \
  "$expected_runtime" "${RUN_VM:-unknown}" "$RUN_STARTED" "$RUN_ENDED" "$RUN_DURATION" "$kubeconform_status" "$kubeconform_digest" >> "$evidence_dir/evidence.txt"

machines="$(env -i PATH="$PATH" HOME="$smolvm_home" XDG_CACHE_HOME="$smolvm_home/.cache" "$smolvm_bin" machine ls --json)"
python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]) == [] else 1)' "$machines" || fail "ephemeral VM cleanup verification failed"
{
  printf 'cleanup: smolvm machine list empty\n'
  printf 'source_commit: %s\n' "$(git -C "$repo_root" rev-parse HEAD)"
  printf 'input_sha256: chart=%s custom_resources=%s crd_schemas=%s\n' "$chart_sha" "$resources_sha" "$schemas_sha"
  printf 'requested_capabilities: network=disabled credentials=none mounts=temporary-read-only-workspace cpu=2 mem_mib=2048 storage_gib=20 timeout_seconds=60\n'
  printf 'effective_capabilities: see helm.stdout negative probes\n'
} >> "$evidence_dir/evidence.txt"

rm -rf "$workdir"
[[ ! -e "$workdir" ]] || fail "temporary workspace cleanup failed"
trap - EXIT

if [[ "$helm_status" -ne 0 || "$kubeconform_status" -ne 0 ]]; then
  printf 'PoC failed; evidence saved to %s\n' "$evidence_dir" >&2
  exit 1
fi

printf 'PoC passed; evidence saved to %s\n' "$evidence_dir"
