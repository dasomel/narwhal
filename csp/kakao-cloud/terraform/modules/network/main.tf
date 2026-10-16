# Network Module - VPC and Subnet
# The v0.3.3 hardcoding workaround was dropped at provider 0.4.4: variables for
# name/cidr_block/subnet now pass provider validation (verified with `tofu validate`
# against 0.4.4 — a literal bad CIDR is still rejected, so the validator does run).

resource "kakaocloud_vpc" "vpc" {
  name       = var.vpc_name
  cidr_block = var.vpc_cidr
}

# D-SUBNET (provider 0.5.0 removed kakaocloud_vpc.subnet): the VPC's default subnet,
# formerly the inline `subnet` block, is managed as its own resource so a clean install
# keeps the same topology and `tofu destroy` does not strand it in front of the VPC.
# Live deployments created on <= 0.4.x must import it once before the first 0.5.x plan:
#   tofu import module.network.kakaocloud_subnet.vpc_default_subnet <subnet_id>
# The name defaults to "main", the name Kakao Cloud gave the inline subnet, so the
# import does not plan a rename. Escape hatch: `tofu state rm` it to leave it unmanaged.
resource "kakaocloud_subnet" "vpc_default_subnet" {
  name              = var.vpc_default_subnet_name
  cidr_block        = var.vpc_default_subnet_cidr
  availability_zone = var.availability_zone
  vpc_id            = kakaocloud_vpc.vpc.id
}

resource "kakaocloud_subnet" "main_subnet" {
  name              = var.subnet_name
  cidr_block        = var.subnet_cidr
  availability_zone = var.availability_zone
  vpc_id            = kakaocloud_vpc.vpc.id
}
