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
  depends_on          = [module.security, kakaocloud_keypair.kpaas_keypair]
}

#####################################################################
# Module 4: LoadBalancer - Master and Worker Load Balancers
#####################################################################
module "loadbalancer" {
  source = "./modules/loadbalancer"

  master_lb_name              = var.master_lb_name
  worker_lb_name              = var.worker_lb_name
  availability_zone           = var.availability_zone
  subnet_id                   = module.network.subnet_id
  master_private_ips          = module.compute.master_private_ips
  worker_private_ips          = module.compute.worker_private_ips
  master_instances_dependency = module.compute.master_instances
  worker_instances_dependency = module.compute.worker_instances
}
