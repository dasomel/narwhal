# LoadBalancer Module

Manages Master and Worker load balancers for Narwhal IDP deployment.

## Resources

### Master LB (K8s API Server)
| Resource | Description |
|----------|-------------|
| `kakaocloud_load_balancer.master_lb` | Master NLB |
| `kakaocloud_public_ip.master_lb_ip` | Master LB public IP |
| `kakaocloud_load_balancer_listener.k8s_api` | K8s API listener (6443) |
| `kakaocloud_load_balancer_listener.etcd` | etcd listener (2379) |
| `kakaocloud_load_balancer_target_group.masters` | K8s API target group |
| `kakaocloud_load_balancer_target_group.etcd` | etcd target group |

### Worker LB (Ingress)
| Resource | Description |
|----------|-------------|
| `kakaocloud_load_balancer.worker_lb` | Worker NLB |
| `kakaocloud_public_ip.worker_lb_ip` | Worker LB public IP |
| `kakaocloud_load_balancer_listener.http` | HTTP listener (80) |
| `kakaocloud_load_balancer_listener.https` | HTTPS listener (443) |
| `kakaocloud_load_balancer_target_group.workers_http` | HTTP target group |
| `kakaocloud_load_balancer_target_group.workers_https` | HTTPS target group |

## Usage

```hcl
module "loadbalancer" {
  source = "./modules/loadbalancer"

  master_lb_name              = "narwhal-master-lb"
  worker_lb_name              = "narwhal-worker-lb"
  availability_zone           = "kr-central-2-a"
  subnet_id                   = module.network.subnet_id
  master_private_ips          = module.compute.master_private_ips
  worker_private_ips          = module.compute.worker_private_ips
  master_instances_dependency = module.compute.master_instances
  worker_instances_dependency = module.compute.worker_instances
}
```

## Outputs

| Name | Description |
|------|-------------|
| master_lb_id | Master LB ID |
| master_lb_vip | Master LB VIP (internal K8s API endpoint) |
| master_lb_public_ip | Master LB public IP (external kubectl) |
| worker_lb_id | Worker LB ID |
| worker_lb_vip | Worker LB VIP (internal) |
| worker_lb_public_ip | Worker LB public IP (external ingress) |

## Notes

- LBs are created in parallel with compute module.
- Target group members use instance private IPs.
- Worker LB routes 80->31080 and 443->31443 (NodePort).
