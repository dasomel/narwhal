# Network Module - VPC and Subnet
# NOTE: Kakao Cloud provider v0.3.3 has a validation bug that prevents using
# variables for name, cidr_block, and subnet attributes in kakaocloud_vpc.
# These must be hardcoded. Edit values below if you need different settings.

resource "kakaocloud_vpc" "vpc" {
  name       = "narwhal-vpc"
  cidr_block = "172.16.0.0/16"

  subnet = {
    cidr_block        = "172.16.255.0/24"
    availability_zone = "kr-central-2-a"
  }
}

resource "kakaocloud_subnet" "main_subnet" {
  name              = var.subnet_name
  cidr_block        = var.subnet_cidr
  availability_zone = var.availability_zone
  vpc_id            = kakaocloud_vpc.vpc.id
}
