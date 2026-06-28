# -*- mode: ruby -*-
# vi: set ft=ruby :

#=========================================
# Domain Configuration (from configs/cluster.env)
#=========================================
BASE_DOMAIN = begin
  env_file = File.join(__dir__, "configs", "cluster.env")
  if File.exist?(env_file)
    line = File.readlines(env_file).find { |l| l =~ /^\s*BASE_DOMAIN\s*=/ }
    line ? line.split("=", 2).last.strip.gsub(/#.*$/, "").strip : "local.narwhal.internal"
  else
    "local.narwhal.internal"
  end
rescue
  "local.narwhal.internal"
end

#=========================================
# Cluster Configuration
#=========================================
CLUSTER_NAME = "narwhal"
K8S_VERSION = "1.35"
K8S_PATCH_VERSION = "1.35.5"
POD_NETWORK_CIDR = "10.244.0.0/16"
SERVICE_CIDR = "10.96.0.0/12"

#=========================================
# Node Configuration
#=========================================
MASTER_COUNT = 3
MASTER_IP_BASE = "192.168.56.1"  # master-1: .10, master-2: .11, master-3: .12
WORKER_IP_BASE = "192.168.56.2"  # worker-1: .21, worker-2: .22, worker-3: .23
WORKER_COUNT = 3

#=========================================
# kube-vip Configuration
#=========================================
VIP_ADDRESS = "192.168.56.100"  # Virtual IP for control plane
VIP_INTERFACE = "eth1"          # Private network interface

#=========================================
# NFS Configuration
#=========================================
NFS_SERVER_IP = "#{MASTER_IP_BASE}0"  # NFS server runs on master-1
NFS_SHARE_PATH = "/srv/nfs/k8s"      # NFS export path

#=========================================
# Resource Configuration
#=========================================
MASTER_CPUS = 2
MASTER_MEMORY = 6144    # 6GB - control plane + DaemonSets headroom
WORKER_CPUS = 2
WORKER_MEMORY = 6144    # 6GB - platform apps

#=========================================
# Disk Configuration
#=========================================
# Minimum 50GB recommended for IDP full deployment
DISK_SIZE_GB = 30  # GB

Vagrant.configure("2") do |config|
  # Base Box (XFS for project quota support)
  config.vm.box = "dasomel/ubuntu-26.04-xfs"
  config.vm.box_version = "0.1.0"
  config.vm.box_check_update = true

  # Disk size plugin (VirtualBox only)
  # Install: vagrant plugin install vagrant-disksize
  if Vagrant.has_plugin?("vagrant-disksize")
    config.disksize.size = "#{DISK_SIZE_GB}GB"
  end

  # SSH
  config.ssh.insert_key = true

  # Graceful shutdown: drain kubelet before halt to reduce stale containerd refs
  config.trigger.before :halt do |trigger|
    trigger.name = "Graceful Kubernetes Shutdown"
    trigger.info = "Stopping kubelet gracefully before VM halt..."
    trigger.run_remote = {inline: "systemctl stop kubelet && sleep 3 || true"}
  end

  # Disable default shared folder
  config.vm.synced_folder ".", "/vagrant", disabled: true

  # Sync scripts, configs, and gitops
  config.vm.synced_folder "scripts/", "/home/vagrant/scripts",
    owner: "vagrant", group: "vagrant"
  config.vm.synced_folder "configs/", "/home/vagrant/configs",
    owner: "vagrant", group: "vagrant"
  config.vm.synced_folder "gitops/", "/home/vagrant/configs/gitops",
    owner: "vagrant", group: "vagrant"

  # Sync the sibling narwhal-portal repo so the in-cluster Kaniko build
  # (15-narwhal-portal.sh -> kaniko-build.sh) has the portal source on the VM.
  # rsync excludes build junk so this stays small (a few MB, not the 1.5G repo).
  if Dir.exist?(File.join(__dir__, "..", "narwhal-portal"))
    config.vm.synced_folder "../narwhal-portal", "/home/vagrant/narwhal-portal",
      type: "rsync",
      rsync__exclude: [".git/", "node_modules/", ".next/", "test-results/"],
      owner: "vagrant", group: "vagrant"
  end

  #=========================================
  # Master Nodes
  #=========================================
  (1..MASTER_COUNT).each do |i|
    master_ip = "#{MASTER_IP_BASE}#{i - 1}"  # master-1: .10, master-2: .11

    config.vm.define "master-#{i}", primary: (i == 1) do |master|
      master.vm.hostname = "#{CLUSTER_NAME}-master-#{i}"
      master.vm.network "private_network", ip: master_ip

      master.vm.provider "virtualbox" do |vb|
        vb.name = "#{CLUSTER_NAME}-master-#{i}"
        vb.cpus = MASTER_CPUS
        vb.memory = MASTER_MEMORY
        vb.linked_clone = true
      end

      master.vm.provider "vmware_desktop" do |vmware|
        vmware.vmx["displayName"] = "#{CLUSTER_NAME}-master-#{i}"
        vmware.vmx["numvcpus"] = MASTER_CPUS
        vmware.vmx["memsize"] = MASTER_MEMORY
        vmware.linked_clone = true
      end

      # Common provisioning (all masters)
      master.vm.provision "shell", path: "scripts/common/01-prerequisites.sh",
        env: {
          "CLUSTER_NAME" => CLUSTER_NAME,
          "MASTER_COUNT" => MASTER_COUNT,
          "MASTER_IP_BASE" => MASTER_IP_BASE,
          "VIP_ADDRESS" => VIP_ADDRESS,
          "WORKER_COUNT" => WORKER_COUNT,
          "WORKER_IP_BASE" => WORKER_IP_BASE,
          "NODE_IP" => master_ip,
          "DOMAIN" => BASE_DOMAIN
        }
      master.vm.provision "shell", path: "scripts/common/02-containerd.sh",
        env: { "DOMAIN" => BASE_DOMAIN }
      master.vm.provision "shell", path: "scripts/common/03-k8s-install.sh",
        env: { "K8S_VERSION" => K8S_VERSION, "K8S_PATCH_VERSION" => K8S_PATCH_VERSION, "DOMAIN" => BASE_DOMAIN }

      # kube-vip (all masters — static pod manifest before kubeadm)
      master.vm.provision "shell", path: "scripts/cluster/00-kube-vip.sh",
        env: {
          "VIP_ADDRESS" => VIP_ADDRESS,
          "NODE_INDEX" => i,
          "DOMAIN" => BASE_DOMAIN
        }

      if i == 1
        #=========================================
        # Master-1: Phase 1 - Cluster Infrastructure
        #=========================================
        master.vm.provision "shell", path: "scripts/cluster/01-nfs-server.sh",
          env: {
            "NFS_SHARE_PATH" => NFS_SHARE_PATH,
            "POD_NETWORK_CIDR" => POD_NETWORK_CIDR,
            "DOMAIN" => BASE_DOMAIN
          }
        master.vm.provision "shell", path: "scripts/cluster/02-init-cluster.sh",
          env: {
            "VIP_ADDRESS" => VIP_ADDRESS,
            "MASTER_COUNT" => MASTER_COUNT,
            "MASTER_IP_BASE" => MASTER_IP_BASE,
            "POD_NETWORK_CIDR" => POD_NETWORK_CIDR,
            "SERVICE_CIDR" => SERVICE_CIDR,
            "DOMAIN" => BASE_DOMAIN
          }
        master.vm.provision "shell", path: "scripts/cluster/03-cni-install.sh",
          env: { "MASTER_IP" => VIP_ADDRESS, "DOMAIN" => BASE_DOMAIN }
        master.vm.provision "shell", path: "scripts/cluster/04-addons.sh",
          env: {
            "NFS_SERVER_IP" => NFS_SERVER_IP,
            "NFS_SHARE_PATH" => NFS_SHARE_PATH,
            "DOMAIN" => BASE_DOMAIN
          }
        master.vm.provision "shell", path: "scripts/cluster/05-nfs-quota-agent.sh",
          env: {
            "NFS_SHARE_PATH" => NFS_SHARE_PATH,
            "MASTER_HOSTNAME" => "#{CLUSTER_NAME}-master-1",
            "DOMAIN" => BASE_DOMAIN
          }

        #=========================================
        # Phase 2: Platform Apps (triggered after all nodes join)
        #=========================================
        master.vm.provision "phase2-platform", type: "shell", run: "never",
          path: "scripts/cluster/06-phase2-start.sh",
          env: {
            "VIP_ADDRESS" => VIP_ADDRESS,
            "MASTER_IP" => master_ip,
            "MASTER_IP_BASE" => MASTER_IP_BASE,
            "MASTER_COUNT" => MASTER_COUNT,
            "METALLB_IP" => "192.168.56.200",
            "DOMAIN" => BASE_DOMAIN
          }
      else
        #=========================================
        # Master-2+: Join control plane only
        #=========================================
        master.vm.provision "shell", path: "scripts/cluster/02-join-control-plane.sh",
          env: {
            "MASTER1_IP" => "#{MASTER_IP_BASE}0",
            "VIP_ADDRESS" => VIP_ADDRESS,
            "DOMAIN" => BASE_DOMAIN
          }

        # Install dnsmasq for DNS HA (skip CoreDNS config — master-1 handles it in phase2)
        master.vm.provision "shell", path: "scripts/cluster/10-dnsmasq.sh",
          env: {
            "MASTER_IP" => master_ip,
            "METALLB_IP" => "192.168.56.200",
            "DOMAIN" => BASE_DOMAIN,
            "SKIP_COREDNS" => "true",
            "MASTER_IP_BASE" => MASTER_IP_BASE,
            "MASTER_COUNT" => MASTER_COUNT
          }
      end
    end
  end

  #=========================================
  # Worker Nodes
  #=========================================
  (1..WORKER_COUNT).each do |i|
    config.vm.define "worker-#{i}" do |worker|
      worker.vm.hostname = "#{CLUSTER_NAME}-worker-#{i}"
      worker.vm.network "private_network", ip: "#{WORKER_IP_BASE}#{i}"

      worker.vm.provider "virtualbox" do |vb|
        vb.name = "#{CLUSTER_NAME}-worker-#{i}"
        vb.cpus = WORKER_CPUS
        vb.memory = WORKER_MEMORY
        vb.linked_clone = true
      end

      worker.vm.provider "vmware_desktop" do |vmware|
        vmware.vmx["displayName"] = "#{CLUSTER_NAME}-worker-#{i}"
        vmware.vmx["numvcpus"] = WORKER_CPUS
        vmware.vmx["memsize"] = WORKER_MEMORY
        vmware.linked_clone = true
      end

      # Common provisioning
      worker.vm.provision "shell", path: "scripts/common/01-prerequisites.sh",
        env: {
          "CLUSTER_NAME" => CLUSTER_NAME,
          "MASTER_COUNT" => MASTER_COUNT,
          "MASTER_IP_BASE" => MASTER_IP_BASE,
          "VIP_ADDRESS" => VIP_ADDRESS,
          "WORKER_COUNT" => WORKER_COUNT,
          "WORKER_IP_BASE" => WORKER_IP_BASE,
          "NODE_IP" => "#{WORKER_IP_BASE}#{i}",
          "DOMAIN" => BASE_DOMAIN
        }
      worker.vm.provision "shell", path: "scripts/common/02-containerd.sh",
        env: { "DOMAIN" => BASE_DOMAIN }
      worker.vm.provision "shell", path: "scripts/common/03-k8s-install.sh",
        env: { "K8S_VERSION" => K8S_VERSION, "K8S_PATCH_VERSION" => K8S_PATCH_VERSION, "DOMAIN" => BASE_DOMAIN }

      # Worker provisioning (join via VIP)
      worker.vm.provision "shell", path: "scripts/cluster/02-join-worker.sh",
        env: { "MASTER_IP" => "#{MASTER_IP_BASE}0", "DOMAIN" => BASE_DOMAIN }

      # Configure worker systemd-resolved to forward *.${BASE_DOMAIN} to master dnsmasq.
      # Without this, image pulls from harbor.${BASE_DOMAIN} fail ("tls: unrecognized name").
      worker.vm.provision "shell", path: "scripts/cluster/10-worker-dns.sh",
        env: {
          "MASTER_IP" => "#{MASTER_IP_BASE}0",
          "DOMAIN"    => BASE_DOMAIN
        }

      # After last worker joins, trigger Phase 2 platform apps on master-1
      if i == WORKER_COUNT
        worker.trigger.after :up do |trigger|
          trigger.name = "Phase 2: Platform Apps"
          trigger.info = "All nodes joined cluster. Installing platform apps on master-1..."
          trigger.run = {inline: "vagrant provision master-1 --provision-with phase2-platform"}
        end
      end
    end
  end
end
