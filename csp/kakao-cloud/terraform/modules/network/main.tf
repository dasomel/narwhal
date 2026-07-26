# Network Module - VPC and Subnet
# The v0.3.3 hardcoding workaround was dropped at provider 0.4.4: variables for
# name/cidr_block/subnet now pass provider validation (verified with `tofu validate`
# against 0.4.4 — a literal bad CIDR is still rejected, so the validator does run).

resource "kakaocloud_vpc" "vpc" {
  name       = var.vpc_name
  cidr_block = var.vpc_cidr

  subnet = {
    cidr_block        = var.vpc_default_subnet_cidr
    availability_zone = var.availability_zone
  }
}

resource "kakaocloud_subnet" "main_subnet" {
  name              = var.subnet_name
  cidr_block        = var.subnet_cidr
  availability_zone = var.availability_zone
  vpc_id            = kakaocloud_vpc.vpc.id
}
