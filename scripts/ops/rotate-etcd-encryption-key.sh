#!/usr/bin/env bash
set -euo pipefail
umask 077

# Run as root (or with sudo) on master-1. State records the completed phase and new key
# name, never key material. Re-running resumes at the last safely completed phase.
DRY_RUN=0
if [[ "${1:-}" == --dry-run ]]; then DRY_RUN=1; shift; fi
if [[ $# -lt 1 ]]; then echo "usage: $0 [--dry-run] master-ip [master-ip ...]" >&2; exit 2; fi
if (( EUID != 0 && DRY_RUN == 0 )); then echo "run this script with sudo so key bytes stay out of sudo arguments" >&2; exit 1; fi
MASTERS=("$@")
CONFIG="${CONFIG:-/etc/kubernetes/enc/encryption-config.yaml}"
MANIFEST="${MANIFEST:-/etc/kubernetes/manifests/kube-apiserver.yaml}"
STATE="${STATE:-/var/lib/narwhal/etcd-encryption-rotation.state}"
KNOWN_HOSTS="${KNOWN_HOSTS:-${HOME}/.ssh/known_hosts}"
if [[ -z "${NODE_SSH_USER:-}" ]]; then
  if [[ "${PROVIDER:-}" == kakao ]]; then NODE_SSH_USER=ubuntu; else NODE_SSH_USER=vagrant; fi
fi
VERIFY="${VERIFY:-/home/vagrant/scripts/verify/etcd-encryption-check.sh}"
PHASE=0
NEW_KEY_NAME=""
if [[ -f "$STATE" ]]; then
  read -r PHASE NEW_KEY_NAME < "$STATE"
  [[ "$PHASE" =~ ^[0-7]$ ]] || { echo "invalid rotation state file: $STATE" >&2; exit 1; }
  if [[ "$PHASE" != 0 && -z "$NEW_KEY_NAME" ]]; then
    echo "legacy rotation state lacks a key name; refusing unsafe index-based recovery" >&2
    exit 1
  fi
fi

step() { printf 'STEP %s: %s\n' "$1" "$2"; }
run() { if (( DRY_RUN )); then printf 'DRY-RUN:'; printf ' %q' "$@"; printf '\n'; else "$@"; fi; }
save_phase() { (( DRY_RUN )) || { sudo install -d -m 700 "$(dirname "$STATE")"; printf '%s %s\n' "$1" "$NEW_KEY_NAME" | sudo tee "$STATE" >/dev/null; sudo chmod 600 "$STATE"; }; PHASE="$1"; }
save_key_name() { (( DRY_RUN )) || { sudo install -d -m 700 "$(dirname "$STATE")"; printf '0 %s\n' "$NEW_KEY_NAME" | sudo tee "$STATE" >/dev/null; sudo chmod 600 "$STATE"; }; }
remote() { local ip="$1" cmd="$2"; if (( DRY_RUN )); then printf "DRY-RUN: ssh %s@%s %q\n" "$NODE_SSH_USER" "$ip" "$cmd"; else sshpass -p "${NODE_SSH_PASSWORD:-vagrant}" ssh -o StrictHostKeyChecking=no "${NODE_SSH_USER}@${ip}" "$cmd"; fi; }
sync_config() {
  local ip="$1"
  if (( DRY_RUN )); then echo "DRY-RUN: stream protected config to ${NODE_SSH_USER}@${ip}:${CONFIG}"; return; fi
  sudo cat "$CONFIG" | sshpass -p "${NODE_SSH_PASSWORD:-vagrant}" ssh -o StrictHostKeyChecking=no "${NODE_SSH_USER}@${ip}" "umask 077; sudo tee '$CONFIG' >/dev/null && sudo chown root:root '$CONFIG' && sudo chmod 600 '$CONFIG' && sudo yq -r '.resources[] | select(.resources | contains([\"secrets\"])) | .providers[] | select(has(\"aescbc\")) | .aescbc.keys[0].secret' '$CONFIG' | sudo tee /etc/kubernetes/enc/encryption-key >/dev/null && sudo chown root:root /etc/kubernetes/enc/encryption-key && sudo chmod 600 /etc/kubernetes/enc/encryption-key"
  if [[ "$ip" == "${MASTERS[0]}" ]]; then
    sudo install -o vagrant -g vagrant -m 600 "$CONFIG" /home/vagrant/encryption-config.yaml
  fi
}

restart_one() {
  local ip="$1"
  remote "$ip" "set -e; manifest='$MANIFEST'; before=\$(sudo crictl ps --name kube-apiserver --state Running -q | head -n1); test -n \"\$before\"; sudo mv \"\$manifest\" \"\$manifest.rotation\"; restored=0; restore_manifest() { if [ \"\$restored\" -eq 0 ]; then sudo mv \"\$manifest.rotation\" \"\$manifest\"; fi; }; trap restore_manifest EXIT; for n in \$(seq 1 60); do if ! sudo crictl ps --name kube-apiserver --state Running 2>/dev/null | grep -q kube-apiserver; then break; fi; sleep 2; done; sudo mv \"\$manifest.rotation\" \"\$manifest\"; restored=1; after=''; for n in \$(seq 1 60); do after=\$(sudo crictl ps --name kube-apiserver --state Running -q | head -n1); if [ -n \"\$after\" ] && [ \"\$after\" != \"\$before\" ] && curl -skf https://127.0.0.1:6443/readyz >/dev/null; then exit 0; fi; sleep 5; done; exit 1"
}

rollout_all() {
  local ip
  for ip in "${MASTERS[@]}"; do echo "  master ${ip}: update and readiness gate"; sync_config "$ip"; restart_one "$ip"; done
}
mutate_config() {
  local mode="$1" new_key="$2" new_name
  new_name="${NEW_KEY_NAME:-key-$(date +%s%N)}"
  NEW_KEY_NAME="$new_name"
  (( DRY_RUN )) && { echo "DRY-RUN: update protected config (${mode}); key bytes withheld"; return; }
  command -v yq >/dev/null || { echo "yq v4 is required" >&2; return 1; }
  if [[ "$mode" == add ]]; then
    if ! NEW_KEY_NAME="$new_name" yq -e '.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys[] | select(.name == strenv(NEW_KEY_NAME))' "$CONFIG" >/dev/null; then
      NEW_KEY="$new_key" NEW_KEY_NAME="$new_name" yq -i '(.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys) += [{"name":strenv(NEW_KEY_NAME),"secret":strenv(NEW_KEY)}]' "$CONFIG"
    fi
  elif [[ "$mode" == promote ]]; then
    NEW_KEY_NAME="$new_name" yq -i '(.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys) |= (map(select(.name == strenv(NEW_KEY_NAME))) + map(select(.name != strenv(NEW_KEY_NAME))))' "$CONFIG"
  else
    NEW_KEY_NAME="$new_name" yq -i '(.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys) |= map(select(.name == strenv(NEW_KEY_NAME)))' "$CONFIG"
  fi
  chown root:root "$CONFIG"; chmod 600 "$CONFIG"
  install -o vagrant -g vagrant -m 600 "$CONFIG" /home/vagrant/encryption-config.yaml
  yq -r '.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys[0].secret' "$CONFIG" > /etc/kubernetes/enc/encryption-key
  chown root:root /etc/kubernetes/enc/encryption-key; chmod 600 /etc/kubernetes/enc/encryption-key
}

echo "Rotation phases: 1 add new key; 2 rollout; 3 promote; 4 rollout; 5 rewrite and verify; 6 remove old key; 7 rollout. State: $STATE (phase $PHASE)."
new_key=""
if (( DRY_RUN )); then new_key='[generated-secret-hidden]';
elif (( PHASE == 0 )); then
  if [[ -n "$NEW_KEY_NAME" ]]; then
    new_key="$(NEW_KEY_NAME="$NEW_KEY_NAME" yq -r '.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys[] | select(.name == strenv(NEW_KEY_NAME)) | .secret' "$CONFIG" 2>/dev/null || true)"
  fi
  if [[ -z "$new_key" || "$new_key" == null ]]; then
    NEW_KEY_NAME="key-$(date +%s%N)"
    new_key="$(head -c 32 /dev/urandom | base64 -w0)"
    save_key_name
  fi
else
  new_key="$(NEW_KEY_NAME="$NEW_KEY_NAME" yq -r '.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys[] | select(.name == strenv(NEW_KEY_NAME)) | .secret' "$CONFIG")"
  [[ -n "$new_key" && "$new_key" != null ]] || { echo "cannot recover key named $NEW_KEY_NAME from $CONFIG; refusing to continue" >&2; exit 1; }
fi
if (( PHASE < 1 )); then
  step 1 'add the new key as a second aescbc key (never print key)'; mutate_config add "$new_key"; save_phase 1
fi
if (( PHASE < 2 )); then step 2 'sync and roll apiservers one at a time, wait /readyz'; rollout_all; save_phase 2; fi
if (( PHASE < 3 )); then step 3 'promote new key to first; retain old key'; mutate_config promote "$new_key"; save_phase 3; fi
if (( PHASE < 4 )); then step 4 'sync promoted config and roll apiservers one at a time'; rollout_all; save_phase 4; fi
if (( PHASE < 5 )); then
  step 5 'rewrite Secrets in namespace batches, then require zero unencrypted etcd values'
  if (( DRY_RUN )); then echo 'DRY-RUN: kubectl get secrets -A grouped by namespace | kubectl replace -f -; run verifier completeness check';
  else
    failed=0
    rotation_stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    error_file="$(mktemp)"
    while IFS=$'\t' read -r ns name; do
      [[ -n "$ns" && -n "$name" ]] || continue
      for attempt in 1 2 3 4 5; do
        if kubectl -n "$ns" get secret "$name" -o json | python3 -c 'import json,sys; x=json.load(sys.stdin); x.setdefault("metadata",{}).setdefault("annotations",{})["narwhal.io/encryption-rotation"]="'"$rotation_stamp"'"; json.dump(x,sys.stdout)' | kubectl replace -f - 2>"$error_file"; then
          break
        fi
        if grep -qi 'immutable' "$error_file"; then
          echo "Could not rewrite immutable Secret $ns/$name: $(cat "$error_file")" >&2
        elif grep -qi 'conflict' "$error_file" && (( attempt < 5 )); then
          sleep "$attempt"
          continue
        else
          echo "Could not rewrite Secret $ns/$name: $(cat "$error_file")" >&2
        fi
        failed=1
        break
      done
    done < <(kubectl get secrets --all-namespaces -o json | python3 -c 'import json,sys; [print(x["metadata"]["namespace"]+"\t"+x["metadata"]["name"]) for x in json.load(sys.stdin).get("items",[])]')
    rm -f "$error_file"
    (( failed == 0 )) || { echo 'One or more Secrets could not be rewritten; old key remains available.' >&2; exit 1; }
    if ! "$VERIFY" --strict "${MASTERS[@]}"; then echo 'Verification failed; old key remains available. Resume after resolving the failure.' >&2; exit 1; fi
  fi
  save_phase 5
fi
if (( PHASE < 6 )); then step 6 'remove old key only after completeness verification passed'; mutate_config remove-old "$new_key"; save_phase 6; fi
if (( PHASE < 7 )); then step 7 'sync final config and roll apiservers one at a time'; rollout_all; save_phase 7; fi
echo 'Rotation phases complete.'
