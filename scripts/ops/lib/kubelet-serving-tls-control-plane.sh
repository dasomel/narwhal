#!/usr/bin/env bash
# Sourced by apply-kubelet-serving-tls.sh to reuse its inventory and SSH transport.
# shellcheck disable=SC2154

control_helper="${helper%/*}/kubelet-serving-tls-control-plane-node.sh"

on_control_plane() {
  local helper="$control_helper"
  on_node "$@"
}

control_plane_preflight() {
  control_indices=() control_ips=()
  local index node ip
  for index in "${!nodes[@]}"; do
    node="${nodes[index]}"
    [[ "$node" =~ ^[a-z0-9.-]+$ ]] || die "invalid node name $node"
    ip=$(node_ip "$node")
    control_ips+=("$ip")
    if jq -e --arg node "$node" '.items[] | select(.metadata.name == $node) |
      (.metadata.labels // {}) | has("node-role.kubernetes.io/control-plane")' "$work/nodes.json" >/dev/null; then
      on_node "$index" read "$node" >/dev/null || die "$node: cannot verify mapped control-plane identity"
      control_indices+=("$index")
    fi
  done
  ((${#control_indices[@]} > 0)) || die "no registered control-plane nodes"
  kube -n kube-system get cm kubeadm-config -o json > "$work/kubeadm.json"
  jq -erj '.data.ClusterConfiguration' "$work/kubeadm.json" > "$work/cluster.yaml"
}

guard_kubelet_rollback() {
  local index
  bash "$control_helper" guard-config "$work/cluster.yaml"
  for index in "${control_indices[@]}"; do
    on_control_plane "$index" guard "${nodes[index]}" "${control_ips[index]}" "$timeout"
  done
}

roll_control_plane() {
  local index
  bash "$control_helper" config "$target" "$work/cluster.yaml" > "$work/cluster-new.yaml"
  for index in "${control_indices[@]}"; do
    on_control_plane "$index" plan "$target" "${nodes[index]}"
    if [[ "$target" == true ]]; then
      on_control_plane "$index" verify "${control_ips[@]}"
    fi
  done
  [[ "$dry_run" == false ]] || return 0

  for index in "${control_indices[@]}"; do
    on_control_plane "$index" write "$target" "${nodes[index]}" "${control_ips[index]}" "$timeout"
  done

  # D-R416-PERSIST: publish only after the static Pods are ready. Cost: a failed
  # write can leave live Pods ahead of kubeadm. Escape: re-run or roll back.
  if ! cmp -s "$work/cluster.yaml" "$work/cluster-new.yaml"; then
    jq -c --rawfile cfg "$work/cluster-new.yaml" '
      [{op:"test",path:"/metadata/resourceVersion",value:.metadata.resourceVersion},
       {op:"replace",path:"/data/ClusterConfiguration",value:$cfg}]' "$work/kubeadm.json" > "$work/cluster-patch.json"
    kube -n kube-system patch cm kubeadm-config --type=json --patch-file "$work/cluster-patch.json" -o json |
      jq -erj '.data.ClusterConfiguration' > "$work/cluster-patched.yaml"
    diff -u "$work/cluster-new.yaml" "$work/cluster-patched.yaml"
  fi
  echo "kubeadm-config: stage 3 API server CA enforcement=$target"
}
