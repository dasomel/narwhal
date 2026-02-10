# -*- mode: ruby -*-
# vi: set ft=ruby :

#=========================================
# Cluster Configuration
#=========================================
CLUSTER_NAME = "narwhal"
K8S_VERSION = "1.35"  # Latest stable version
POD_NETWORK_CIDR = "10.244.0.0/16"
SERVICE_CIDR = "10.96.0.0/12"

#=========================================
# Node Configuration
#=========================================
MASTER_IP = "192.168.56.10"
WORKER_IP_BASE = "192.168.56.2"  # worker-1: .21, worker-2: .22
WORKER_COUNT = 2

#=========================================
# kube-vip Configuration
#=========================================
VIP_ADDRESS = "192.168.56.100"  # Virtual IP for control plane
VIP_INTERFACE = "eth1"          # Private network interface

#=========================================
# NFS Configuration
#=========================================
NFS_SERVER_IP = MASTER_IP       # NFS server runs on master
NFS_SHARE_PATH = "/srv/nfs/k8s" # NFS export path

#=========================================
# Resource Configuration
#=========================================
MASTER_CPUS = 2
MASTER_MEMORY = 4096
WORKER_CPUS = 2
WORKER_MEMORY = 4096

#=========================================
# Disk Configuration
#=========================================
# Minimum 50GB recommended for IDP full deployment
DISK_SIZE_GB = 30  # GB

Vagrant.configure("2") do |config|
  # Base Box (XFS for project quota support)
  config.vm.box = "dasomel/ubuntu-24.04-xfs"
  config.vm.box_version = "0.2.0"
  config.vm.box_check_update = true

  # Disk size plugin (VirtualBox only)
  # Install: vagrant plugin install vagrant-disksize
  if Vagrant.has_plugin?("vagrant-disksize")
    config.disksize.size = "#{DISK_SIZE_GB}GB"
  end

  # SSH
  config.ssh.insert_key = true

  # Disable default shared folder
  config.vm.synced_folder ".", "/vagrant", disabled: true

  # Sync scripts, configs, and gitops
  config.vm.synced_folder "scripts/", "/home/vagrant/scripts",
    owner: "vagrant", group: "vagrant"
  config.vm.synced_folder "configs/", "/home/vagrant/configs",
    owner: "vagrant", group: "vagrant"
  config.vm.synced_folder "gitops/", "/home/vagrant/configs/gitops",
    owner: "vagrant", group: "vagrant"

  #=========================================
  # Master Node
  #=========================================
  config.vm.define "master", primary: true do |master|
    master.vm.hostname = "#{CLUSTER_NAME}-master"
    master.vm.network "private_network", ip: MASTER_IP

    master.vm.provider "virtualbox" do |vb|
      vb.name = "#{CLUSTER_NAME}-master"
      vb.cpus = MASTER_CPUS
      vb.memory = MASTER_MEMORY
      vb.linked_clone = true
    end

    master.vm.provider "vmware_desktop" do |vmware|
      vmware.vmx["displayName"] = "#{CLUSTER_NAME}-master"
      vmware.vmx["numvcpus"] = MASTER_CPUS
      vmware.vmx["memsize"] = MASTER_MEMORY
      # Use linked clone for stability (disk expansion done inside VM)
      vmware.linked_clone = true
    end

    # Common provisioning
    # 00-expand-disk.sh: Expands partition/LVM to use available disk space
    master.vm.provision "shell", path: "scripts/common/00-expand-disk.sh"
    master.vm.provision "shell", path: "scripts/common/01-prerequisites.sh",
      env: {
        "CLUSTER_NAME" => CLUSTER_NAME,
        "MASTER_IP" => MASTER_IP,
        "WORKER_COUNT" => WORKER_COUNT,
        "WORKER_IP_BASE" => WORKER_IP_BASE
      }
    master.vm.provision "shell", path: "scripts/common/02-containerd.sh"
    master.vm.provision "shell", path: "scripts/common/03-k8s-install.sh",
      env: { "K8S_VERSION" => K8S_VERSION }
    master.vm.provision "shell", path: "scripts/common/04-nfs-client.sh"

    # Master provisioning
    master.vm.provision "shell", path: "scripts/master/00-nfs-server.sh",
      env: {
        "NFS_SHARE_PATH" => NFS_SHARE_PATH,
        "POD_NETWORK_CIDR" => POD_NETWORK_CIDR
      }
    master.vm.provision "shell", path: "scripts/master/01-kube-vip.sh",
      env: {
        "VIP_ADDRESS" => VIP_ADDRESS,
        "VIP_INTERFACE" => VIP_INTERFACE
      }
    master.vm.provision "shell", path: "scripts/master/02-init-cluster.sh",
      env: {
        "MASTER_IP" => MASTER_IP,
        "POD_NETWORK_CIDR" => POD_NETWORK_CIDR,
        "SERVICE_CIDR" => SERVICE_CIDR
      }
    master.vm.provision "shell", path: "scripts/master/03-cni-install.sh",
      env: { "MASTER_IP" => MASTER_IP }
    master.vm.provision "shell", path: "scripts/master/04-addons.sh",
      env: {
        "NFS_SERVER_IP" => NFS_SERVER_IP,
        "NFS_SHARE_PATH" => NFS_SHARE_PATH
      }
    master.vm.provision "shell", path: "scripts/master/05-nfs-quota-agent.sh",
      env: {
        "NFS_SHARE_PATH" => NFS_SHARE_PATH,
        "MASTER_HOSTNAME" => "#{CLUSTER_NAME}-master"
      }
    master.vm.provision "shell", path: "scripts/master/06-cnpg.sh"
    master.vm.provision "shell", path: "scripts/master/07-keycloak.sh"
    master.vm.provision "shell", path: "scripts/master/08-platform-apps.sh"
    # dnsmasq for local DNS (after MetalLB/Traefik are installed)
    master.vm.provision "shell", path: "scripts/master/09-dnsmasq.sh",
      env: {
        "MASTER_IP" => MASTER_IP,
        "METALLB_IP" => "192.168.56.200",
        "DOMAIN" => "local.narwhal.io"
      }
    # GitOps (Gitea + ArgoCD) - installed last for optional GitOps management
    master.vm.provision "shell", path: "scripts/master/10-gitea.sh"
    master.vm.provision "shell", path: "scripts/master/11-argocd.sh"
    master.vm.provision "shell", path: "scripts/master/12-gitops-bootstrap.sh"
    # Platform apps installed via Helm in 06-platform-apps.sh
    # ArgoCD can adopt existing releases later if needed
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
        # Use linked clone for stability
        vmware.linked_clone = true
      end

      # Common provisioning
      worker.vm.provision "shell", path: "scripts/common/00-expand-disk.sh"
      worker.vm.provision "shell", path: "scripts/common/01-prerequisites.sh",
        env: {
          "CLUSTER_NAME" => CLUSTER_NAME,
          "MASTER_IP" => MASTER_IP,
          "WORKER_COUNT" => WORKER_COUNT,
          "WORKER_IP_BASE" => WORKER_IP_BASE
        }
      worker.vm.provision "shell", path: "scripts/common/02-containerd.sh"
      worker.vm.provision "shell", path: "scripts/common/03-k8s-install.sh",
        env: { "K8S_VERSION" => K8S_VERSION }
      worker.vm.provision "shell", path: "scripts/common/04-nfs-client.sh"

      # Worker provisioning
      worker.vm.provision "shell", path: "scripts/worker/01-join-cluster.sh",
        env: { "MASTER_IP" => MASTER_IP }
    end
  end
end
