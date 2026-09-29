# Security Module Variables

variable "security_group_name" {
  description = "Security group name"
  type        = string
}

variable "description" {
  description = "Security group description"
  type        = string
  default     = "Narwhal IDP security group (Terraform managed)"
}

variable "vpc_cidr" {
  description = "VPC CIDR block for internal traffic rules"
  type        = string
  default     = "172.16.0.0/16"
}

variable "subnet_cidr" {
  description = "Main subnet CIDR block, for rules that only need to reach cluster nodes, not the whole VPC (e.g. the VPC's own auto-created default subnet, where the bastion sits)"
  type        = string
  default     = "172.16.0.0/24"
}
