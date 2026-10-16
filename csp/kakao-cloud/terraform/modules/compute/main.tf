# Compute Module - Master and Worker VM Instances

data "kakaocloud_images" "images_all" {}
data "kakaocloud_instance_flavors" "flavors_all" {}

locals {
  ubuntu24_id = [
    for image in data.kakaocloud_images.images_all.images : image.id
    if image.name == var.image_name
  ][0]

  master_flavor_id = [
    for flavor in data.kakaocloud_instance_flavors.flavors_all.instance_flavors : flavor.id
    if flavor.name == var.master_flavor
  ][0]

  worker_flavor_id = [
    for flavor in data.kakaocloud_instance_flavors.flavors_all.instance_flavors : flavor.id
    if flavor.name == var.worker_flavor
  ][0]
}

# Dedicated data volume for the NFS export on master-1 (XFS + prjquota).
# D-NFS-VOL: holds every PVC served by nfs-quota-agent. Cost: billed while it exists, and
# `tofu destroy` / size 0 fail until this guard is removed. Why: the data must outlive
# VM replacement (it is attached with is_delete_on_termination=false) and one stray
# destroy must not wipe the platform's state. Escape hatch: delete this block's lifecycle
# guard after a verified backup, or `tofu state rm` the volume (see README).
resource "kakaocloud_volume" "nfs_data" {
  count             = var.nfs_data_volume_size > 0 ? 1 : 0
  name              = "${var.master_name}-1-nfs-data"
  availability_zone = var.availability_zone
  size              = var.nfs_data_volume_size

  lifecycle {
    prevent_destroy = true
  }
}

# Master node instances
resource "kakaocloud_instance" "master" {
  count       = var.master_count
  name        = "${var.master_name}-${count.index + 1}"
  description = "Narwhal master node (control-plane)"
  flavor_id   = local.master_flavor_id
  image_id    = local.ubuntu24_id
  key_name    = var.key_name

  # Fixed contiguous private IPs (master-1 = offset, master-2 = offset+1, ...)
  # so the install scripts' ${MASTER_IP_BASE}${idx} derivation works unchanged.
  subnets = [{ id = var.subnet_id, private_ip = cidrhost(var.subnet_cidr, var.master_ip_offset + count.index) }]

  initial_security_groups = [{
    name = var.security_group_name
  }]

  # master-1 only gets the NFS data volume. Both branches share one object type; the
  # root element keeps its id from state (UseStateForUnknown), so adding the data volume
  # later is an in-place AttachVolume, never a replacement.
  volumes = count.index == 0 && var.nfs_data_volume_size > 0 ? [
    { size = var.volume_size, id = null, is_delete_on_termination = null },
    { size = null, id = kakaocloud_volume.nfs_data[0].id, is_delete_on_termination = false },
    ] : [
    { size = var.volume_size, id = null, is_delete_on_termination = null },
  ]

  user_data = var.cloud_init_base64
}


# Worker node instances
resource "kakaocloud_instance" "worker" {
  count       = var.worker_count
  name        = "${var.worker_name}-${count.index + 1}"
  description = "Narwhal worker node (data-plane)"
  flavor_id   = local.worker_flavor_id
  image_id    = local.ubuntu24_id
  key_name    = var.key_name

  # Fixed contiguous private IPs (worker-1 = offset, worker-2 = offset+1, ...)
  subnets = [{ id = var.subnet_id, private_ip = cidrhost(var.subnet_cidr, var.worker_ip_offset + count.index) }]

  initial_security_groups = [{
    name = var.security_group_name
  }]

  volumes = [{ size = var.volume_size }]

  user_data = var.cloud_init_base64
}



#####################################################################
# Node egress
#
# Nodes reach apt, GitHub releases and Helm repos through the squid forward
# proxy on the bastion (scripts/cloud/setup-bastion-proxy.sh) — provider 0.5.1 (and 0.4.4 before it)
# exposes no NAT gateway resource, and a public IP per node would expose the
# SSH and 6443 rules that this security group opens to 0.0.0.0/0.
#
# These stay here as an escape hatch: flip assign_node_public_ips to true and
# the nodes get direct egress without the proxy. Off by default.
#####################################################################
resource "kakaocloud_public_ip" "master" {
  count       = var.assign_node_public_ips ? var.master_count : 0
  description = "Egress for ${var.master_name}-${count.index + 1}"

  related_resource = {
    device_id   = kakaocloud_instance.master[count.index].id
    device_type = "instance"
    id          = kakaocloud_instance.master[count.index].addresses[0].network_interface_id
  }

  # D-PUBIP: external endpoints (DNS, kubeconfig, operator SSH, allowlists) hang on this
  # address. Replace VMs with `tofu apply -replace=...`, never a targeted destroy. Cost:
  # `tofu destroy` and assign_node_public_ips=false fail until this guard is removed.
  # Escape hatch: delete this block, or `tofu state rm` the address (see README Cleanup).
  lifecycle {
    prevent_destroy = true
  }
}

resource "kakaocloud_public_ip" "worker" {
  count       = var.assign_node_public_ips ? var.worker_count : 0
  description = "Egress for ${var.worker_name}-${count.index + 1}"

  related_resource = {
    device_id   = kakaocloud_instance.worker[count.index].id
    device_type = "instance"
    id          = kakaocloud_instance.worker[count.index].addresses[0].network_interface_id
  }

  # D-PUBIP: external endpoints (DNS, kubeconfig, operator SSH, allowlists) hang on this
  # address. Replace VMs with `tofu apply -replace=...`, never a targeted destroy. Cost:
  # `tofu destroy` and assign_node_public_ips=false fail until this guard is removed.
  # Escape hatch: delete this block, or `tofu state rm` the address (see README Cleanup).
  lifecycle {
    prevent_destroy = true
  }
}
