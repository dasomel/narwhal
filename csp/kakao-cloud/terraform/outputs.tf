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

# `ssh -i KEY -J user@bastion user@node` does NOT work: -J starts a separate ssh
# process for the jump host and the command line's -i is not passed to it, so the
# bastion hop fails with "Permission denied (publickey)". Neither does -o IdentityFile.
# The key has to be named for BOTH hops, which is what ProxyCommand below does — and
# what an ssh_config entry does more readably (see the ssh_config output).
# Paths are absolute because var.ssh_key_path is relative to this directory.
output "bastion_ssh" {
  description = "Ready-to-run ssh commands. ProxyCommand, not -J: the jump hop needs the key named explicitly."
  value = {
    bastion = "ssh -i ${abspath(var.ssh_key_path)} ubuntu@${kakaocloud_public_ip.bastion_public.public_ip}"
    masters = [for ip in module.compute.master_private_ips :
    "ssh -i ${abspath(var.ssh_key_path)} -o ProxyCommand='ssh -i ${abspath(var.ssh_key_path)} -W %h:%p ubuntu@${kakaocloud_public_ip.bastion_public.public_ip}' ubuntu@${ip}"]
    workers = [for ip in module.compute.worker_private_ips :
    "ssh -i ${abspath(var.ssh_key_path)} -o ProxyCommand='ssh -i ${abspath(var.ssh_key_path)} -W %h:%p ubuntu@${kakaocloud_public_ip.bastion_public.public_ip}' ubuntu@${ip}"]
  }
}

# Append to ~/.ssh/config and every hop gets the key and the jump host by name:
#   tofu output -raw ssh_config >> ~/.ssh/config
#   ssh narwhal-master-1
#   ssh -f -N -L 6443:127.0.0.1:6443 narwhal-master-1
# ProxyJump works here because ssh_config applies IdentityFile to the bastion entry
# too — the limitation is command-line -J only.
output "ssh_config" {
  description = "ssh_config block for the bastion and every node. Append to ~/.ssh/config."
  value = join("\n", concat(
    [
      "Host narwhal-bastion",
      "  HostName ${kakaocloud_public_ip.bastion_public.public_ip}",
      "  User ubuntu",
      "  IdentityFile ${abspath(var.ssh_key_path)}",
      "  StrictHostKeyChecking accept-new",
      "",
    ],
    flatten([for idx, ip in module.compute.master_private_ips : [
      "Host narwhal-master-${idx + 1}",
      "  HostName ${ip}",
      "  User ubuntu",
      "  IdentityFile ${abspath(var.ssh_key_path)}",
      "  ProxyJump narwhal-bastion",
      "  StrictHostKeyChecking accept-new",
      "",
    ]]),
    flatten([for idx, ip in module.compute.worker_private_ips : [
      "Host narwhal-worker-${idx + 1}",
      "  HostName ${ip}",
      "  User ubuntu",
      "  IdentityFile ${abspath(var.ssh_key_path)}",
      "  ProxyJump narwhal-bastion",
      "  StrictHostKeyChecking accept-new",
      "",
    ]])
  ))
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
