# LoadBalancer Module - Master and Worker Load Balancers
# This module manages Load Balancers for K8s API Server and Worker nodes

# The NLB flavor is looked up in the ROOT module and injected. Keeping the data
# source here would put it behind this module's depends_on, so any pending change
# in module.compute would defer it to apply time, make flavor_id unknown and force
# both load balancers to be replaced — which changes the API server VIP under a
# running cluster. See the depends_on comment in ../../main.tf.

#####################################################################
# Master Load Balancer (K8s API Server + Ingress)
#####################################################################

resource "kakaocloud_load_balancer" "master_lb" {
  name              = var.master_lb_name
  description       = "Load Balancer for K8s API Server"
  availability_zone = var.availability_zone
  subnet_id         = var.subnet_id
  flavor_id         = var.lb_flavor_id
}

# K8s API Server Listener (TCP 6443)
resource "kakaocloud_load_balancer_listener" "k8s_api" {
  load_balancer_id = kakaocloud_load_balancer.master_lb.id
  protocol         = "TCP"
  protocol_port    = 6443
}

# etcd Listener (TCP 2379)
resource "kakaocloud_load_balancer_listener" "etcd" {
  load_balancer_id = kakaocloud_load_balancer.master_lb.id
  protocol         = "TCP"
  protocol_port    = 2379
}

# HTTP Listener on Master LB (Ingress via hostNetwork)
resource "kakaocloud_load_balancer_listener" "master_http" {
  load_balancer_id = kakaocloud_load_balancer.master_lb.id
  protocol         = "TCP"
  protocol_port    = 80
}

# HTTPS Listener on Master LB (Ingress via hostNetwork)
resource "kakaocloud_load_balancer_listener" "master_https" {
  load_balancer_id = kakaocloud_load_balancer.master_lb.id
  protocol         = "TCP"
  protocol_port    = 443
}

# Master Target Group for K8s API
resource "kakaocloud_load_balancer_target_group" "masters" {
  name                    = "masters-tg"
  description             = "Target group for K8s API Server"
  load_balancer_id        = kakaocloud_load_balancer.master_lb.id
  listener_id             = kakaocloud_load_balancer_listener.k8s_api.id
  protocol                = "TCP"
  load_balancer_algorithm = "ROUND_ROBIN"
}

# etcd Target Group
resource "kakaocloud_load_balancer_target_group" "etcd" {
  name                    = "etcd-tg"
  description             = "Target group for etcd"
  load_balancer_id        = kakaocloud_load_balancer.master_lb.id
  listener_id             = kakaocloud_load_balancer_listener.etcd.id
  protocol                = "TCP"
  load_balancer_algorithm = "ROUND_ROBIN"
}

# Master Ingress Target Group (HTTP)
resource "kakaocloud_load_balancer_target_group" "masters_http" {
  name                    = "masters-http-tg"
  description             = "Target group for Ingress HTTP on master nodes"
  load_balancer_id        = kakaocloud_load_balancer.master_lb.id
  listener_id             = kakaocloud_load_balancer_listener.master_http.id
  protocol                = "TCP"
  load_balancer_algorithm = "ROUND_ROBIN"
}

# Master Ingress Target Group (HTTPS)
resource "kakaocloud_load_balancer_target_group" "masters_https" {
  name                    = "masters-https-tg"
  description             = "Target group for Ingress HTTPS on master nodes"
  load_balancer_id        = kakaocloud_load_balancer.master_lb.id
  listener_id             = kakaocloud_load_balancer_listener.master_https.id
  protocol                = "TCP"
  load_balancer_algorithm = "ROUND_ROBIN"
}

# Master Target Group Members (K8s API)
resource "kakaocloud_load_balancer_target_group_member" "master_members" {
  for_each        = { for i, ip in var.master_private_ips : i => ip }
  target_group_id = kakaocloud_load_balancer_target_group.masters.id
  address         = each.value
  subnet_id       = var.subnet_id
  protocol_port   = 6443
  weight          = 1
}

# etcd Target Group Members
resource "kakaocloud_load_balancer_target_group_member" "etcd_members" {
  for_each        = { for i, ip in var.master_private_ips : i => ip }
  target_group_id = kakaocloud_load_balancer_target_group.etcd.id
  address         = each.value
  subnet_id       = var.subnet_id
  protocol_port   = 2379
  weight          = 1
}

# Master Ingress Target Group Members (HTTP) - NodePort
resource "kakaocloud_load_balancer_target_group_member" "master_http_members" {
  for_each        = { for i, ip in var.master_private_ips : i => ip }
  target_group_id = kakaocloud_load_balancer_target_group.masters_http.id
  address         = each.value
  subnet_id       = var.subnet_id
  protocol_port   = 31080
  weight          = 1
}

# Master Ingress Target Group Members (HTTPS) - NodePort
resource "kakaocloud_load_balancer_target_group_member" "master_https_members" {
  for_each        = { for i, ip in var.master_private_ips : i => ip }
  target_group_id = kakaocloud_load_balancer_target_group.masters_https.id
  address         = each.value
  subnet_id       = var.subnet_id
  protocol_port   = 31443
  weight          = 1
}

#####################################################################
# Worker Load Balancer (Ingress) - No health monitors (k-paas style)
#####################################################################

resource "kakaocloud_load_balancer" "worker_lb" {
  name              = var.worker_lb_name
  description       = "Load Balancer for K8s worker nodes"
  availability_zone = var.availability_zone
  subnet_id         = var.subnet_id
  flavor_id         = var.lb_flavor_id
}

# HTTP Listener (TCP 80)
resource "kakaocloud_load_balancer_listener" "http" {
  load_balancer_id = kakaocloud_load_balancer.worker_lb.id
  protocol         = "TCP"
  protocol_port    = 80
}

# HTTPS Listener (TCP 443)
resource "kakaocloud_load_balancer_listener" "https" {
  load_balancer_id = kakaocloud_load_balancer.worker_lb.id
  protocol         = "TCP"
  protocol_port    = 443
}

# Worker Target Group (HTTP)
resource "kakaocloud_load_balancer_target_group" "workers_http" {
  name                    = "workers-http-tg"
  description             = "Target group for worker nodes HTTP"
  load_balancer_id        = kakaocloud_load_balancer.worker_lb.id
  listener_id             = kakaocloud_load_balancer_listener.http.id
  protocol                = "TCP"
  load_balancer_algorithm = "ROUND_ROBIN"
}

# Worker Target Group (HTTPS)
resource "kakaocloud_load_balancer_target_group" "workers_https" {
  name                    = "workers-https-tg"
  description             = "Target group for worker nodes HTTPS"
  load_balancer_id        = kakaocloud_load_balancer.worker_lb.id
  listener_id             = kakaocloud_load_balancer_listener.https.id
  protocol                = "TCP"
  load_balancer_algorithm = "ROUND_ROBIN"
}

# Worker Target Group Members (HTTP) - NodePort
resource "kakaocloud_load_balancer_target_group_member" "worker_http_members" {
  for_each        = { for i, ip in var.worker_private_ips : i => ip }
  target_group_id = kakaocloud_load_balancer_target_group.workers_http.id
  address         = each.value
  subnet_id       = var.subnet_id
  protocol_port   = 31080
  weight          = 1
}

# Worker Target Group Members (HTTPS) - NodePort
resource "kakaocloud_load_balancer_target_group_member" "worker_https_members" {
  for_each        = { for i, ip in var.worker_private_ips : i => ip }
  target_group_id = kakaocloud_load_balancer_target_group.workers_https.id
  address         = each.value
  subnet_id       = var.subnet_id
  protocol_port   = 31443
  weight          = 1
}

#####################################################################
# Public IPs for Load Balancers (external access / testing)
#####################################################################

# Master LB Public IP (external K8s API + Ingress)
resource "kakaocloud_public_ip" "master_lb_public" {
  description = "Public IP for Narwhal master LB (K8s API 6443 / Ingress)"

  related_resource = {
    device_id   = kakaocloud_load_balancer.master_lb.id
    device_type = "load-balancer"
  }
}

# Worker LB Public IP (external Ingress)
resource "kakaocloud_public_ip" "worker_lb_public" {
  description = "Public IP for Narwhal worker LB (Ingress 80/443)"

  related_resource = {
    device_id   = kakaocloud_load_balancer.worker_lb.id
    device_type = "load-balancer"
  }
}

#=========================================
# Health monitors
#=========================================
# Without these the NLB round-robins to every member regardless of whether anything is
# listening. During bring-up only master-1 has an apiserver, so ~2 of every 3 connections
# to the control-plane VIP were refused — and `kubeadm join` on master-2 goes through that
# VIP. It presented as an unreachable VIP rather than a partially-populated target group,
# which is a much harder thing to see.
#
# TCP rather than HTTP: the apiserver serves HTTPS with a cert the LB has no reason to
# trust, and a completed TCP handshake on 6443 is exactly the condition that matters —
# something is accepting connections on this node.
resource "kakaocloud_load_balancer_health_monitor" "masters" {
  target_group_id  = kakaocloud_load_balancer_target_group.masters.id
  type             = "TCP"
  delay            = 5
  timeout          = 3
  max_retries      = 2
  max_retries_down = 3
}

resource "kakaocloud_load_balancer_health_monitor" "etcd" {
  target_group_id  = kakaocloud_load_balancer_target_group.etcd.id
  type             = "TCP"
  delay            = 5
  timeout          = 3
  max_retries      = 2
  max_retries_down = 3
}

# The worker groups front APISIX NodePorts (31080/31443). A node whose APISIX pod has not
# started yet must not receive traffic — with one replica that is most of the fleet.
resource "kakaocloud_load_balancer_health_monitor" "workers_http" {
  target_group_id  = kakaocloud_load_balancer_target_group.workers_http.id
  type             = "TCP"
  delay            = 5
  timeout          = 3
  max_retries      = 2
  max_retries_down = 3
}

resource "kakaocloud_load_balancer_health_monitor" "workers_https" {
  target_group_id  = kakaocloud_load_balancer_target_group.workers_https.id
  type             = "TCP"
  delay            = 5
  timeout          = 3
  max_retries      = 2
  max_retries_down = 3
}
