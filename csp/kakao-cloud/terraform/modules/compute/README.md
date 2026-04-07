# Compute Module

Manages Master and Worker VM instances for Narwhal IDP deployment.

## Resources

- `kakaocloud_instance.master` - Master node instances (control-plane)
- `kakaocloud_instance.worker` - Worker node instances (data-plane)
- `kakaocloud_public_ip.master_ip` - Master node public IPs
- `kakaocloud_public_ip.worker_ip` - Worker node public IPs

## Usage

```hcl
module "compute" {
  source = "./modules/compute"

  master_name         = "narwhal-master"
  worker_name         = "narwhal-worker"
  master_count        = 3
  worker_count        = 3
  image_name          = "Ubuntu 24.04"
  master_flavor       = "t1i.large"
  worker_flavor       = "t1i.xlarge"
  volume_size         = 200
  key_name            = "my-keypair"
  subnet_id           = module.network.subnet_id
  security_group_name = module.security.security_group_name
  cloud_init_base64   = filebase64("cloud-init.yaml")
}
```

## Inputs

| Name | Description | Type | Default |
|------|-------------|------|---------|
| master_name | Master node name prefix | string | "master" |
| worker_name | Worker node name prefix | string | "worker" |
| master_count | Number of master nodes | number | 3 |
| worker_count | Number of worker nodes | number | 3 |
| image_name | OS image name | string | "Ubuntu 24.04" |
| master_flavor | Master instance flavor | string | - |
| worker_flavor | Worker instance flavor | string | - |
| volume_size | Boot volume size (GB) | number | 200 |
| key_name | SSH key pair name | string | - |
| subnet_id | Subnet ID to attach instances | string | - |
| security_group_name | Security group name | string | - |
| cloud_init_base64 | Base64 encoded cloud-init data | string | - |

## Outputs

| Name | Description |
|------|-------------|
| master_instance_ids | Master instance ID list |
| master_private_ips | Master private IP list |
| master_public_ips | Master public IP list |
| worker_instance_ids | Worker instance ID list |
| worker_private_ips | Worker private IP list |
| worker_public_ips | Worker public IP list |

## Notes

- Network and security group must be created before instances.
- cloud-init file must be base64 encoded.
- Flavors must be valid Kakao Cloud instance types.
