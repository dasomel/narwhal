# Narwhal IDP - Kakao Cloud Infrastructure
# Creates VPC, Subnet, Security Group, VMs, and Load Balancers
# K8s and platform apps are installed separately after VM provisioning

#####################################################################
# Module 1: Network - VPC and Subnet
#####################################################################
module "network" {
  source = "./modules/network"

  vpc_name                = var.vpc_name
  vpc_cidr                = var.vpc_cidr
  vpc_default_subnet_cidr = var.vpc_default_subnet_cidr
  subnet_name             = var.subnet_name
  subnet_cidr             = var.subnet_cidr
  availability_zone       = var.availability_zone
}

#####################################################################
# Module 2: Security - Security Group
#####################################################################
module "security" {
  source = "./modules/security"

  security_group_name = var.security_group_name
  vpc_cidr            = module.network.vpc_cidr
  depends_on          = [module.network]
}

#####################################################################
# Key Pair Creation
#####################################################################
resource "tls_private_key" "kpaas_key" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "kakaocloud_keypair" "kpaas_keypair" {
  name       = var.key_name
  public_key = tls_private_key.kpaas_key.public_key_openssh
}

resource "local_sensitive_file" "kpaas_key_file" {
  content         = tls_private_key.kpaas_key.private_key_pem
  filename        = var.ssh_key_path
  file_permission = "0600"
}

#####################################################################
# Module 3: Compute - Master and Worker Instances
#####################################################################
module "compute" {
  source = "./modules/compute"

  master_name         = var.master_name
  worker_name         = var.worker_name
  master_count        = var.master_count
  worker_count        = var.worker_count
  image_name          = var.image_name
  master_flavor       = var.master_flavor
  worker_flavor       = var.worker_flavor
  volume_size         = var.volume_size
  key_name            = var.key_name
  subnet_id           = module.network.subnet_id
  subnet_cidr         = var.subnet_cidr
  security_group_name = module.security.security_group_name
  cloud_init_base64   = filebase64("${path.module}/cloud-init.yaml")

  # Nodes need outbound internet for apt, GitHub releases and Helm repos; there is
  # no NAT gateway in provider 0.4.4, so a public IP per node is the egress path.
  assign_node_public_ips = var.assign_node_public_ips

  # Only the keypair. module.security is deliberately NOT listed: the instances
  # already depend on it through security_group_name, so naming it here added no
  # ordering — but a module-level depends_on also defers this module's DATA
  # SOURCES to apply time whenever a dependency has pending changes. That made
  # data.kakaocloud_images unreadable at plan time, image_id unknown, and every
  # instance "must be replaced" for nothing more than an added security group
  # rule. Keep this list as narrow as the real dependency.
  depends_on = [kakaocloud_keypair.kpaas_keypair]
}

#####################################################################
# Module 4: LoadBalancer - Master and Worker Load Balancers
#####################################################################
# Resolved here, not inside the module: a data source under a module-level
# depends_on is deferred to apply time whenever that dependency has pending
# changes, which would make flavor_id unknown and force LB replacement.
data "kakaocloud_load_balancer_flavors" "all" {}

locals {
  lb_flavor_nlb_id = one([
    for f in data.kakaocloud_load_balancer_flavors.all.flavors : f.id
    if f.name == "NLB"
  ])
}

module "loadbalancer" {
  source = "./modules/loadbalancer"

  lb_flavor_id       = local.lb_flavor_nlb_id
  master_lb_name     = var.master_lb_name
  worker_lb_name     = var.worker_lb_name
  availability_zone  = var.availability_zone
  subnet_id          = module.network.subnet_id
  master_private_ips = module.compute.master_private_ips
  worker_private_ips = module.compute.worker_private_ips

  # Load balancers must not exist before the nodes do. A kakaocloud_load_balancer
  # takes its private VIP from this same subnet by automatic allocation
  # (private_vip is computed — it cannot be pinned, and kakaocloud_subnet exposes
  # no allocation pool to carve out), so an LB created first can grab an address
  # the compute module has pinned. That is exactly what happened on 2026-07-26:
  # master_lb took 172.16.0.12 twenty seconds before the instances started, and
  # master-3 died with "Fixed IP address 172.16.0.12 is already in use".
  #
  # The module used to receive master/worker_instances_dependency for this, but
  # those variables were declared and never referenced, so no ordering edge ever
  # existed. This depends_on is the real thing.
  depends_on = [module.compute]
}
