# Narwhal IDP on Kakao Cloud - Terraform Infrastructure

English | [Korean](README.ko.md)

Terraform project that provisions VM infrastructure on Kakao Cloud for the Narwhal IDP cluster. K8s and platform apps are installed separately after VM creation.

## What Gets Created

| Resource | Count | Description |
|----------|-------|-------------|
| VPC | 1 | 172.16.0.0/16 |
| Subnet | 1 | 172.16.0.0/24 |
| Security Group | 1 | K8s + Cilium + NFS ports |
| Master VMs | 3 | Control-plane nodes (t1i.large, 4GB) |
| Worker VMs | 3 | Data-plane nodes (t1i.xlarge, 8GB) |
| Master LB | 1 | K8s API Server (6443) + etcd (2379) |
| Worker LB | 1 | Ingress HTTP (80) + HTTPS (443) |
| Public IPs | 8 | 6 VMs + 2 LBs |

## Architecture

```
                              Internet
                                  |
                +-----------------+------------------+
                |                                    |
          Master LB                            Worker LB
     (<Public IP>:6443)               (<Public IP>:80/443)
                |                                    |
     +----------+----------+              +----------+----------+
     |          |          |              |          |          |
  Master-1  Master-2  Master-3       Worker-1  Worker-2  Worker-3
     |          |          |              |          |          |
     +----------+----------+--------------+----------+----------+
                            |
                      VPC: 172.16.0.0/16
                   Subnet: 172.16.0.0/24
```

## Prerequisites

- **OpenTofu** >= 1.6.0 (`tofu`; Terraform CLI also works)
- **Kakao Cloud Account** with Application Credential (ID + Secret)
- **SSH KeyPair** registered in Kakao Cloud
- **Quotas**: 6 instances, 24 vCPU, 48GB+ RAM, 1.2TB volume, 8 public IPs, 2 LBs

## Quick Start

```bash
cd csp/kakao-cloud/terraform

# Configure
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your credentials

# Deploy
tofu init
tofu plan
tofu apply

# For slow API environments
tofu apply -parallelism=3
```

## After Deployment

VMs are created with Ubuntu 24.04 and SSH key access. Install K8s and Narwhal platform separately:

```bash
# Get connection info
tofu output

# SSH to master-1
ssh -i <your-key.pem> ubuntu@$(tofu output -json master_public_ips | jq -r '.[0]')
```

## Directory Structure

```
terraform/
  main.tf              # Root: network -> security -> compute -> loadbalancer
  variables.tf         # Variable definitions
  outputs.tf           # VM IPs, LB endpoints, SSH commands
  provider.tf          # kakaocloud provider v0.4.4
  cloud-init.yaml      # Basic VM init (SSH pubkey only)
  terraform.tfvars.example
  modules/
    network/           # VPC + Subnet
    security/          # Security Group (Cilium ports)
    compute/           # Master/Worker VMs + Public IPs
    loadbalancer/      # Master LB (6443) + Worker LB (80/443)
```

## Configuration

Key settings in `terraform.tfvars`:

| Variable | Default | Description |
|----------|---------|-------------|
| `master_count` | 3 | Master nodes (3 for HA) |
| `worker_count` | 3 | Worker nodes |
| `master_flavor` | t1i.large | 2 vCPU, 4GB RAM |
| `worker_flavor` | t1i.xlarge | 4 vCPU, 8GB RAM |
| `volume_size` | 200 | Disk size in GB |
| `availability_zone` | kr-central-2-a | Kakao Cloud AZ |

## Deployment Timeline

| Module | Duration | Notes |
|--------|----------|-------|
| Network | ~60 min | Kakao Cloud API is slow for VPC creation |
| Security | ~5 min | |
| Compute | ~15 min | 6 VMs + 6 public IPs |
| LoadBalancer | ~10 min | 2 NLBs + target groups |
| **Total** | **~90 min** | |

## Cleanup

```bash
tofu destroy
```

## Known Issues

None open. The v0.3.3 `kakaocloud_vpc` bug that forced hardcoded VPC values was fixed in
v0.4.4 — see `modules/network/README.md` for the verification.

## Version Info

- **OpenTofu**: >= 1.6.0
- **Provider**: kakaocloud v0.4.4
- **OS**: Ubuntu 24.04 LTS
