# Bastion Host - SSH jump host for private node access
# Nodes have no public IP; management SSH goes through this single hardened
# entry point. External data paths (K8s API / Ingress) are served by the LBs.
# Reuses the existing narwhal-sg (SSH 22 already open) - no SG change needed.
# Self-contained at root level so it can be removed without touching modules.

data "kakaocloud_images" "bastion_image" {}
data "kakaocloud_instance_flavors" "bastion_flavors" {}

locals {
  bastion_image_id = [
    for image in data.kakaocloud_images.bastion_image.images : image.id
    if image.name == var.image_name
  ][0]

  bastion_flavor_id = [
    for flavor in data.kakaocloud_instance_flavors.bastion_flavors.instance_flavors : flavor.id
    if flavor.name == var.bastion_flavor
  ][0]
}

resource "kakaocloud_instance" "bastion" {
  name        = var.bastion_name
  description = "Narwhal bastion (SSH jump host)"
  flavor_id   = local.bastion_flavor_id
  image_id    = local.bastion_image_id
  key_name    = var.key_name

  subnets = [{ id = module.network.subnet_id }]

  initial_security_groups = [{
    name = module.security.security_group_name
  }]

  volumes = [{ size = var.bastion_volume_size }]

  user_data  = filebase64("${path.module}/cloud-init.yaml")
  depends_on = [module.security, kakaocloud_keypair.kpaas_keypair]
}

resource "kakaocloud_public_ip" "bastion_public" {
  description = "Public IP for Narwhal bastion (SSH jump host)"

  related_resource = {
    device_id   = kakaocloud_instance.bastion.id
    device_type = "instance"
    id          = kakaocloud_instance.bastion.addresses[0].network_interface_id
  }
}
