# Narwhal IDP - Kakao Cloud Variables

# =============================================
# Authentication
# =============================================
variable "application_credential_id" {
  description = "Kakao Cloud Application Credential ID"
  type        = string
}

variable "application_credential_secret" {
  description = "Kakao Cloud Application Credential Secret"
  type        = string
  sensitive   = true
}

# =============================================
# Node Configuration
# =============================================
variable "master_name" {
  description = "Master node name prefix"
  type        = string
  default     = "narwhal-master"
}

variable "worker_name" {
  description = "Worker node name prefix"
  type        = string
  default     = "narwhal-worker"
}

variable "master_count" {
  description = "Number of master nodes (1 or 3 recommended for HA)"
  type        = number
  default     = 3

  validation {
    condition     = var.master_count >= 1 && var.master_count <= 5
    error_message = "master_count must be between 1 and 5 (3 recommended for HA)"
  }
}

variable "worker_count" {
  description = "Number of worker nodes (minimum 1)"
  type        = number
  default     = 3

  validation {
    condition     = var.worker_count >= 1 && var.worker_count <= 10
    error_message = "worker_count must be between 1 and 10"
  }
}

# =============================================
# SSH Configuration
# =============================================
variable "key_name" {
  description = "SSH key pair name registered in Kakao Cloud"
  type        = string
}

variable "ssh_key_path" {
  description = "Path to SSH private key for instance access"
  type        = string
  default     = "~/.ssh/id_rsa"
}

# =============================================
# Network Configuration
# =============================================
variable "vpc_name" {
  description = "VPC name"
  type        = string
  default     = "narwhal-vpc"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "172.16.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid CIDR block (e.g., 172.16.0.0/16)"
  }
}

variable "vpc_default_subnet_cidr" {
  description = "VPC default subnet CIDR"
  type        = string
  default     = "172.16.255.0/24"

  validation {
    condition     = can(cidrhost(var.vpc_default_subnet_cidr, 0))
    error_message = "vpc_default_subnet_cidr must be a valid CIDR block"
  }
}

variable "subnet_name" {
  description = "Main subnet name"
  type        = string
  default     = "narwhal-subnet"
}

variable "subnet_cidr" {
  description = "Main subnet CIDR block"
  type        = string
  default     = "172.16.0.0/24"

  validation {
    condition     = can(cidrhost(var.subnet_cidr, 0))
    error_message = "subnet_cidr must be a valid CIDR block"
  }
}

variable "availability_zone" {
  description = "Availability zone"
  type        = string
  default     = "kr-central-2-a"
}

# =============================================
# Compute Configuration
# =============================================
variable "image_name" {
  description = "OS image name"
  type        = string
  default     = "Ubuntu 24.04"
}

variable "master_flavor" {
  description = "Master instance flavor (4GB+ recommended for control-plane)"
  type        = string
  default     = "t1i.large"
}

variable "worker_flavor" {
  description = "Worker instance flavor (6GB+ recommended for platform apps)"
  type        = string
  default     = "t1i.xlarge"
}

variable "volume_size" {
  description = "Boot volume size in GB"
  type        = number
  default     = 200

  validation {
    condition     = var.volume_size >= 50 && var.volume_size <= 1000
    error_message = "volume_size must be between 50 and 1000 GB"
  }
}

# =============================================
# Security
# =============================================
variable "security_group_name" {
  description = "Security group name"
  type        = string
  default     = "narwhal-sg"
}

# =============================================
# Load Balancer
# =============================================
variable "master_lb_name" {
  description = "Master load balancer name (K8s API Server)"
  type        = string
  default     = "narwhal-master-lb"
}

variable "worker_lb_name" {
  description = "Worker load balancer name (Ingress traffic)"
  type        = string
  default     = "narwhal-worker-lb"
}

# =============================================
# Bastion (SSH jump host for private node access)
# =============================================
variable "bastion_name" {
  description = "Bastion host name"
  type        = string
  default     = "narwhal-bastion"
}

variable "bastion_flavor" {
  description = "Bastion instance flavor (small is enough for an SSH jump host)"
  type        = string
  default     = "t1i.small"
}

variable "bastion_volume_size" {
  description = "Bastion boot volume size in GB"
  type        = number
  default     = 30

  validation {
    condition     = var.bastion_volume_size >= 20 && var.bastion_volume_size <= 200
    error_message = "bastion_volume_size must be between 20 and 200 GB"
  }
}
