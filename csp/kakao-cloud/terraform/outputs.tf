# Narwhal IDP - Outputs

#####################################################################
# Network
#####################################################################
output "vpc_id" {
  description = "VPC ID"
  value       = module.network.vpc_id
}

output "subnet_id" {
  description = "Subnet ID"
  value       = module.network.subnet_id
}

#####################################################################
# Security
#####################################################################
output "security_group_id" {
  description = "Security Group ID"
  value       = module.security.security_group_id
}

#####################################################################
# Compute
#####################################################################
output "master_instance_ids" {
  description = "Master instance IDs"
  value       = module.compute.master_instance_ids
}

output "master_private_ips" {
  description = "Master instance private IPs"
  value       = module.compute.master_private_ips
}

output "master_public_ips" {
  description = "Master node public IPs"
  value       = module.compute.master_public_ips
}

output "worker_instance_ids" {
  description = "Worker instance IDs"
  value       = module.compute.worker_instance_ids
}

output "worker_private_ips" {
  description = "Worker instance private IPs"
  value       = module.compute.worker_private_ips
}

output "worker_public_ips" {
  description = "Worker node public IPs"
  value       = module.compute.worker_public_ips
}

#####################################################################
# Load Balancer
#####################################################################
output "master_lb_id" {
  description = "Master Load Balancer ID"
  value       = module.loadbalancer.master_lb_id
}

output "master_lb_vip" {
  description = "Master Load Balancer VIP (K8s API Server endpoint)"
  value       = module.loadbalancer.master_lb_vip
}

output "master_lb_public_ip" {
  description = "Master Load Balancer Public IP (external kubectl access)"
  value       = module.loadbalancer.master_lb_public_ip
}

output "worker_lb_id" {
  description = "Worker Load Balancer ID"
  value       = module.loadbalancer.worker_lb_id
}

output "worker_lb_vip" {
  description = "Worker Load Balancer VIP"
  value       = module.loadbalancer.worker_lb_vip
}

output "worker_lb_public_ip" {
  description = "Worker Load Balancer Public IP"
  value       = module.loadbalancer.worker_lb_public_ip
}

#####################################################################
# Connection Info (for post-provisioning)
#####################################################################
output "ssh_connection" {
  description = "SSH connection commands for each node"
  value = {
    master_nodes = [for i, ip in module.compute.master_public_ips : "ssh ubuntu@${coalesce(ip, "N/A")}" if ip != null]
    worker_nodes = [for i, ip in module.compute.worker_public_ips : "ssh ubuntu@${coalesce(ip, "N/A")}" if ip != null]
    note         = "If empty, public IPs are not assigned. Use master LB public IP for access."
  }
}

output "k8s_api_endpoints" {
  description = "Kubernetes API Server endpoints (available after K8s installation)"
  value = {
    internal = "https://${coalesce(module.loadbalancer.master_lb_vip, "N/A")}:6443"
    external = "https://${coalesce(module.loadbalancer.master_lb_public_ip, "N/A")}:6443"
  }
}

#####################################################################
# Bastion (SSH jump host)
#####################################################################
output "bastion_public_ip" {
  description = "Bastion public IP (SSH jump host)"
  value       = kakaocloud_public_ip.bastion_public.public_ip
}

output "bastion_private_ip" {
  description = "Bastion private IP"
  value       = kakaocloud_instance.bastion.addresses[0].private_ip
}

output "bastion_ssh" {
  description = "SSH into the bastion, and ProxyJump commands to reach private nodes"
  value = {
    bastion = "ssh -i ${var.ssh_key_path} ubuntu@${kakaocloud_public_ip.bastion_public.public_ip}"
    masters = [for ip in module.compute.master_private_ips : "ssh -i ${var.ssh_key_path} -J ubuntu@${kakaocloud_public_ip.bastion_public.public_ip} ubuntu@${ip}"]
    workers = [for ip in module.compute.worker_private_ips : "ssh -i ${var.ssh_key_path} -J ubuntu@${kakaocloud_public_ip.bastion_public.public_ip} ubuntu@${ip}"]
  }
}

output "vpc_cidr" {
  description = "VPC CIDR block (used by the cloud helper scripts for proxy ACLs and NO_PROXY)"
  value       = module.network.vpc_cidr
}

output "ssh_key_path" {
  description = "Absolute path to the generated private key. Absolute on purpose: var.ssh_key_path is relative to this directory, and the helper scripts run from the repo root."
  value       = abspath(var.ssh_key_path)
}

output "subnet_cidr" {
  description = "Node subnet CIDR. The provisioning scripts need it for HOST_NETWORK_CIDR — the NFS export ACL is built from it, and a Vagrant-shaped default (192.168.56.0/24) makes every CSI mount fail."
  value       = module.network.subnet_cidr
}
