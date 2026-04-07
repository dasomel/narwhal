# Network Module

Manages VPC and Subnet for Narwhal IDP deployment.

## Resources

- `kakaocloud_vpc` - VPC
- `kakaocloud_subnet` - Main subnet

## Usage

```hcl
module "network" {
  source = "./modules/network"

  vpc_name          = "narwhal-vpc"
  vpc_cidr          = "172.16.0.0/16"
  subnet_name       = "narwhal-subnet"
  subnet_cidr       = "172.16.0.0/24"
  availability_zone = "kr-central-2-a"
}
```

## Inputs

| Name | Description | Type | Default |
|------|-------------|------|---------|
| vpc_name | VPC name | string | "narwhal-vpc" |
| vpc_cidr | VPC CIDR block | string | "172.16.0.0/16" |
| subnet_name | Subnet name | string | "narwhal-subnet" |
| subnet_cidr | Subnet CIDR block | string | "172.16.0.0/24" |
| availability_zone | Availability zone | string | "kr-central-2-a" |

## Outputs

| Name | Description |
|------|-------------|
| vpc_id | VPC ID |
| vpc_cidr | VPC CIDR block |
| subnet_id | Subnet ID |
| subnet_cidr | Subnet CIDR block |
| availability_zone | Availability zone |

## Known Issue: Provider v0.3.3 VPC Bug

Kakao Cloud provider v0.3.3 has a validation bug where `kakaocloud_vpc` rejects Terraform variables for `name`, `cidr_block`, and `subnet` attributes. These are hardcoded in `main.tf`. Edit that file directly if you need different values.

Affected: `kakaocloud_vpc` resource only. `kakaocloud_subnet` works with variables normally.
