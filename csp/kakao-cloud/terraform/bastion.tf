# Bastion Host - SSH jump host for private node access
# Nodes have no public IP; management SSH goes through this single hardened
# entry point. External data paths (K8s API / Ingress) are served by the LBs.
# Self-contained at root level so it can be removed without touching modules.
#
# Dedicated security group (issue #188): the bastion used to reuse narwhal-sg,
# the same group attached to master/worker nodes. That group carries 0.0.0.0/0
# ingress for 6443 and 30000-32767 for the nodes' own LB target-group health
# checks/data-plane traffic; a bastion with a public IP inherited those rules
# too, even though it runs neither the API server nor NodePort services. The
# bastion only needs: SSH in from an explicit operator allowlist, and the
# VPC-internal ports its own bootstrap services (squid/dnsmasq/registry) serve
# to the private nodes.
resource "kakaocloud_security_group" "bastion_sg" {
  name        = "${var.bastion_name}-sg"
  description = "Narwhal bastion SG - SSH ingress restricted to operator CIDRs; no public 6443/NodePort/HTTP(S)"

  # Built with concat() rather than a static list so the SSH allowlist can be a
  # variable-length list of CIDRs (one ingress rule per entry) without hand
  # writing N near-duplicate map literals; every other rule stays a plain list
  # entry to match the style of modules/security.
  rules = concat([
    for cidr in var.bastion_ssh_allowed_cidrs : {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 22
      port_range_max   = 22
      remote_ip_prefix = cidr
      description      = "SSH access (operator-restricted)"
    }
    ], [
    {
      direction        = "ingress"
      protocol         = "ICMP"
      remote_ip_prefix = "0.0.0.0/0"
      description      = "ICMP (Ping) for reachability checks"
    },
    # Forward proxy on the bastion (squid). Nodes have no NAT gateway, so apt/
    # GitHub/Helm egress from the private nodes is relayed through here.
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 3128
      port_range_max   = 3128
      remote_ip_prefix = var.vpc_cidr
      description      = "Bastion forward proxy (squid), internal only"
    },
    # Airgap bootstrap registry (registry:2) until Harbor is up.
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 5000
      port_range_max   = 5000
      remote_ip_prefix = var.vpc_cidr
      description      = "Airgap bootstrap registry, internal only"
    },
    # Split DNS (dnsmasq) - see modules/security for why both TCP and UDP.
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 53
      port_range_max   = 53
      remote_ip_prefix = var.vpc_cidr
      description      = "Bastion split DNS (dnsmasq), internal only"
    },
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 53
      port_range_max   = 53
      remote_ip_prefix = var.vpc_cidr
      description      = "Bastion split DNS (dnsmasq) TCP, internal only"
    },
    {
      direction        = "egress"
      protocol         = "ALL"
      remote_ip_prefix = "0.0.0.0/0"
      description      = "All outbound traffic"
    }
  ])
}

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
    name = kakaocloud_security_group.bastion_sg.name
  }]

  volumes = [{ size = var.bastion_volume_size }]

  user_data  = filebase64("${path.module}/cloud-init.yaml")
  depends_on = [kakaocloud_security_group.bastion_sg, kakaocloud_keypair.kpaas_keypair]
}

resource "kakaocloud_public_ip" "bastion_public" {
  description = "Public IP for Narwhal bastion (SSH jump host)"

  related_resource = {
    device_id   = kakaocloud_instance.bastion.id
    device_type = "instance"
    id          = kakaocloud_instance.bastion.addresses[0].network_interface_id
  }
}
