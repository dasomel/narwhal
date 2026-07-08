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

  volumes = [{ size = var.volume_size }]

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

