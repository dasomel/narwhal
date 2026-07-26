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

## Resolved: Provider v0.3.3 VPC Bug

On provider v0.3.3 `kakaocloud_vpc` rejected variables for `name`, `cidr_block`, and `subnet`,
so those three were hardcoded in `main.tf`. **Fixed in v0.4.4** — all VPC attributes are
variable-driven again, so change them through `terraform.tfvars`, not by editing `main.tf`.

Verified offline against the pinned 0.4.4 provider: `tofu validate` passes with variables (both
with and without defaults), while a literal malformed CIDR is still rejected with
`Invalid IP CIDR String Value` — proving the provider validator runs and no longer trips on
variables.
