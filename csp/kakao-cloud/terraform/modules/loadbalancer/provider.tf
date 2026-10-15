terraform {
  required_version = ">= 1.13.0"
  required_providers {
    # Kakao Cloud Provider 설정
    kakaocloud = {
      source  = "registry.terraform.io/kakaoenterprise/kakaocloud"
      version = "0.5.1"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}
