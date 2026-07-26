# Compute Module Variables

variable "master_name" {
  description = "Master node name prefix"
  type        = string
  default     = "master"
}

variable "worker_name" {
  description = "Worker node name prefix"
  type        = string
  default     = "worker"
}

variable "master_count" {
  description = "Number of master nodes"
  type        = number
  default     = 3
}

variable "worker_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 3
}

variable "image_name" {
  description = "OS image name"
  type        = string
  default     = "Ubuntu 24.04"
}

variable "master_flavor" {
  description = "Master instance flavor"
  type        = string
}

variable "worker_flavor" {
  description = "Worker instance flavor"
  type        = string
}

variable "volume_size" {
  description = "Boot volume size in GB"
  type        = number
  default     = 200
}

variable "key_name" {
  description = "SSH key pair name"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID to attach instances"
  type        = string
}

variable "subnet_cidr" {
  description = "Subnet CIDR block (used to derive contiguous fixed private IPs)"
  type        = string
}

variable "master_ip_offset" {
  description = "Host offset for master-1's fixed IP in the subnet (master-i = offset + i-1). Matches Vagrant scheme so existing install scripts' MASTER_IP_BASE logic works unchanged."
  type        = number
  default     = 10
}

variable "worker_ip_offset" {
  description = "Host offset for worker-1's fixed IP in the subnet (worker-i = offset + i-1)."
  type        = number
  default     = 21
}

variable "security_group_name" {
  description = "Security group name to apply"
  type        = string
}

variable "cloud_init_base64" {
  description = "Base64 encoded cloud-init user data"
  type        = string
}

variable "assign_node_public_ips" {
  description = "Attach a public IP to every master/worker instead of using the bastion forward proxy. Default off — nodes stay private."
  type        = bool
  default     = false
}
