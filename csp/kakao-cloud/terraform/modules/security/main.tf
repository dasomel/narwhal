# Security Module - Security Group for Narwhal IDP

resource "kakaocloud_security_group" "security_group" {
  name        = var.security_group_name
  description = var.description

  rules = [
    # SSH
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 22
      port_range_max   = 22
      remote_ip_prefix = "0.0.0.0/0"
      description      = "SSH access"
    },
    # Kubernetes API Server
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 6443
      port_range_max   = 6443
      remote_ip_prefix = "0.0.0.0/0"
      description      = "Kubernetes API Server"
    },
    # etcd
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 2379
      port_range_max   = 2380
      remote_ip_prefix = var.vpc_cidr
      description      = "etcd cluster communication (internal)"
    },
    # Kubelet API
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 10250
      port_range_max   = 10250
      remote_ip_prefix = var.vpc_cidr
      description      = "Kubelet API (internal)"
    },
    # K8s Scheduler & Controller Manager
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 10251
      port_range_max   = 10252
      remote_ip_prefix = var.vpc_cidr
      description      = "K8s Scheduler and Controller Manager (internal)"
    },
    # NodePort Services
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 30000
      port_range_max   = 32767
      remote_ip_prefix = "0.0.0.0/0"
      description      = "NodePort services"
    },
    # HTTP
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 80
      port_range_max   = 80
      remote_ip_prefix = "0.0.0.0/0"
      description      = "HTTP traffic"
    },
    # HTTPS
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 443
      port_range_max   = 443
      remote_ip_prefix = "0.0.0.0/0"
      description      = "HTTPS traffic"
    },
    # NFS rpcbind TCP/UDP
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 111
      port_range_max   = 111
      remote_ip_prefix = var.vpc_cidr
      description      = "NFS rpcbind TCP (internal)"
    },
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 111
      port_range_max   = 111
      remote_ip_prefix = var.vpc_cidr
      description      = "NFS rpcbind UDP (internal)"
    },
    # NFS TCP/UDP
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 2049
      port_range_max   = 2049
      remote_ip_prefix = var.vpc_cidr
      description      = "NFS TCP (internal)"
    },
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 2049
      port_range_max   = 2049
      remote_ip_prefix = var.vpc_cidr
      description      = "NFS UDP (internal)"
    },
    # Cilium VXLAN
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 8472
      port_range_max   = 8472
      remote_ip_prefix = var.vpc_cidr
      description      = "Cilium VXLAN overlay (internal)"
    },
    # Cilium health check
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 4240
      port_range_max   = 4240
      remote_ip_prefix = var.vpc_cidr
      description      = "Cilium health check (internal)"
    },
    # Cilium Hubble
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 4244
      port_range_max   = 4245
      remote_ip_prefix = var.vpc_cidr
      description      = "Cilium Hubble relay/server (internal)"
    },
    # ICMP
    {
      direction        = "ingress"
      protocol         = "ICMP"
      remote_ip_prefix = "0.0.0.0/0"
      description      = "ICMP (Ping)"
    },
    # Forward proxy on the bastion. Nodes sit on a private subnet with no NAT
    # (provider 0.4.4 has no NAT gateway resource), so apt, GitHub releases and
    # Helm repos are reached through squid on the bastion instead of giving every
    # node a public IP. VPC-internal only — the proxy is never exposed publicly.
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 3128
      port_range_max   = 3128
      remote_ip_prefix = var.vpc_cidr
      description      = "Bastion forward proxy (squid), internal only"
    },
    # Airgap bootstrap registry (registry:2 on the bastion). Hosts every mirrored
    # image until Harbor is up, so containerd on every node pulls from it.
    # VPC-internal only.
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 5000
      port_range_max   = 5000
      remote_ip_prefix = var.vpc_cidr
      description      = "Airgap bootstrap registry, internal only"
    },
    # Split DNS for *.<DOMAIN>, served by dnsmasq on the bastion. Vagrant points every
    # node's systemd-resolved at master-1's dnsmasq for this zone; the cloud had no
    # equivalent, so the nodes could not resolve a service name at all and Phase 2 scripts
    # that curl one failed with HTTP 000. The bastion plays master-1's role here: it is up
    # before the cluster and squid on the same host then resolves these names too.
    # UDP carries almost every query; TCP is needed for responses over 512 bytes.
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 53
      port_range_max   = 53
      remote_ip_prefix = var.vpc_cidr
      description      = "Bastion split DNS (dnsmasq), internal only"
    },
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 53
      port_range_max   = 53
      remote_ip_prefix = var.vpc_cidr
      description      = "Bastion split DNS (dnsmasq) TCP, internal only"
    },
    # NFSv3 helper ports. v4.1 deadlocked (nfsd4_destroy_session waiting on the callback
    # workqueue while the client waits in nfs4_destroy_clientid for the reply), so the
    # storage class moved to v3 — which is stateless but does its locking over NLM, and NLM
    # grants travel FROM the server TO the client. Every one of these is both directions
    # between nodes, so they are opened on all of them rather than just the server.
    #
    # This has to be explicit because the group drops unlisted ports instead of refusing
    # them: a lock callback to a closed port fails immediately, but to a dropped port it
    # hangs, which is the failure mode that cost this cluster a day.
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 20048
      port_range_max   = 20048
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 mountd TCP, internal only"
    },
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 20048
      port_range_max   = 20048
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 mountd UDP, internal only"
    },
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 4045
      port_range_max   = 4045
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 lockd (NLM) TCP, internal only"
    },
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 4045
      port_range_max   = 4045
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 lockd (NLM) UDP, internal only"
    },
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 4047
      port_range_max   = 4047
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 statd TCP, internal only"
    },
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 4047
      port_range_max   = 4047
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 statd UDP, internal only"
    },
    {
      direction        = "ingress"
      protocol         = "TCP"
      port_range_min   = 4048
      port_range_max   = 4048
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 statd outgoing TCP, internal only"
    },
    {
      direction        = "ingress"
      protocol         = "UDP"
      port_range_min   = 4048
      port_range_max   = 4048
      remote_ip_prefix = var.vpc_cidr
      description      = "NFSv3 statd outgoing UDP, internal only"
    },
    # All Egress
    {
      direction        = "egress"
      protocol         = "ALL"
      remote_ip_prefix = "0.0.0.0/0"
      description      = "All outbound traffic"
    }
  ]
}
