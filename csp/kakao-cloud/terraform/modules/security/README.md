# Security Module

Manages security group for Narwhal IDP deployment.

## Ingress Rules

| Port | Scope | Protocol | Purpose |
|------|-------|----------|---------|
| 22 | external | TCP | SSH access |
| 80 | external | TCP | HTTP traffic |
| 443 | external | TCP | HTTPS traffic |
| 6443 | external | TCP | Kubernetes API Server |
| 30000-32767 | external | TCP | NodePort services |
| ICMP | external | ICMP | Ping |
| 111 | internal | TCP/UDP | NFS rpcbind |
| 2049 | internal | TCP/UDP | NFS |
| 2379-2380 | internal | TCP | etcd |
| 4240 | internal | TCP | Cilium health check |
| 4244-4245 | internal | TCP | Cilium Hubble |
| 8472 | internal | UDP | Cilium VXLAN overlay |
| 10250 | internal | TCP | Kubelet API |
| 10251-10252 | internal | TCP | K8s Scheduler/Controller Manager |

## Egress Rules

- **ALL**: All outbound traffic allowed

## Usage

```hcl
module "security" {
  source = "./modules/security"

  security_group_name = "narwhal-sg"
  vpc_cidr            = "172.16.0.0/16"
}
```

## Notes

- Internal rules use VPC CIDR as source.
- Uses Cilium CNI ports (not Calico).
- Consider restricting SSH source IP in production.
