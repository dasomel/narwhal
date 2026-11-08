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
# The patch release every node is pinned to, and the value
# scripts/airgap/01-generate-image-list.sh reads to build the offline bundle. Was 1.35.5
# while clusters actually ran 1.35.7, because 03-k8s-install.sh only printed it and let
# apt choose; both now agree. Bump only alongside a verified clean install.
K8S_PATCH_VERSION = "1.35.7"
POD_NETWORK_CIDR = "10.244.0.0/16"
SERVICE_CIDR = "10.96.0.0/12"

#=========================================
# Airgap
#=========================================
# AIRGAP=1 makes the provisioners install from the bundle instead of the internet:
# OS packages from a file:// APT repo, Helm charts from the mounted tarballs, container
# images from the mirror registry configured by scripts/airgap/06-configure-mirrors.sh.
# Off by default — a plain `vagrant up` is still an ordinary online install.
AIRGAP = ENV.fetch("AIRGAP", "0")
# Which bundle to mount. Apple Silicon hosts build arm64; override for an amd64 box.
AIRGAP_ARCH_SUFFIX = ENV.fetch("AIRGAP_ARCH_SUFFIX", "arm64")
# Where the guests reach the mirror registry. The host-only address is the host itself,
# so a registry served from the Mac needs no VM of its own and exists before any node boots.
#
# 5001, not the registry default 5000: macOS ControlCenter (AirPlay Receiver) listens on
# 5000 on every recent release and answers 403, which looks like a broken registry rather
# than the wrong service. Verified with lsof on 2026-08-04.
AIRGAP_REGISTRY = ENV.fetch("AIRGAP_REGISTRY", "192.168.56.1:5001")

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
# D-NFS-VMDK (owner decision 2026-11-08): the NFS export lives on its OWN virtual disk, not on master-1's
# root disk. 2026-11-08: all 17 nfs-csi PVs shared one VMDK with etcd, an NFS write burst pushed the etcd
# WAL fsync past 8s, the apiserver timed out and CNPG restarted/failed over. Two VMDKs are two separate
# I/O queues in the guest, but both files still sit on the same host SSD, so this reduces the coupling
# (queue depth, fsync ordering inside one guest FS); it does not isolate host I/O.
# The disk is a growable (sparse) vmdk/vdi: the host only allocates what is written, a ceiling not a
# reservation. 200 is a ceiling: the 17 PVs hold well under 100GB, and a vmdk never shrinks
# (operations.md section 12). The size only applies when the disk is created; it is never resized here.
# D-NFS-VMDK-VMX (2026-11-08, live): on vmware_desktop NOT config.vm.disk. vagrant-vmware-desktop 3.0.5
# crashes in cap/disk.rb get_disk (File.extname(nil)) on this box's vmx, which breaks every master-1 boot.
# Instead a before-up trigger creates narwhal-nfs.vmdk with vmware-vdiskmanager (-a lsilogic -t 0, growable
# single file) and provider vmx keys attach it as nvme0:1 (the box OS disk is nvme0:0; the guest then sees
# the data disk as nvme0n1 and the OS disk as nvme0n2, root is LVM by UUID so that is harmless).
# Plugin cleanup_disks is a no-op because no disk is in config (disk_meta stays {}), so it cannot delete it.
# Cost: no automatic cleanup; escape hatch: delete the vmdk by hand. NARWHAL_NFS_DISK_GB=0 = do not
# create/attach (cluster that never had the disk).
require "fileutils"
NFS_DISK_GB = Integer(ENV.fetch("NARWHAL_NFS_DISK_GB", "200"), exception: false)
abort "NARWHAL_NFS_DISK_GB must be an integer >= 0 (got #{ENV['NARWHAL_NFS_DISK_GB'].inspect})" unless NFS_DISK_GB && NFS_DISK_GB >= 0
NFS_VMDK_NAME = "narwhal-nfs.vmdk"  # never rename: the live master-1 vmx references this name
# Where the vmdk lives. Existing machine: its VM dir (dirname of the vmx recorded in .vagrant/.../id), referenced
# by bare file name. Otherwise (first `up`, the VM dir does not exist yet) an absolute path under .vagrant/,
# stable across later boots because the VM dir never gains the file.
NFS_MACHINE_DIR = File.join(__dir__, ".vagrant/machines/master-1/vmware_desktop")
NFS_VM_DIR = begin
  id_file = File.join(NFS_MACHINE_DIR, "id")
  File.exist?(id_file) ? File.dirname(File.read(id_file).strip) : nil
rescue StandardError
  nil
end
NFS_VMDK_IN_VMDIR = !NFS_VM_DIR.nil? && File.exist?(File.join(NFS_VM_DIR, NFS_VMDK_NAME))
NFS_VMDK_ABS = File.join(__dir__, ".vagrant/narwhal-nfs", NFS_VMDK_NAME)
NFS_VMDK_VMX = NFS_VMDK_IN_VMDIR ? NFS_VMDK_NAME : NFS_VMDK_ABS
# D-NFS-VMDK-GUARD: NARWHAL_NFS_DISK_GB=0 on a machine that already has the disk would boot without it and
# export the stale root-disk copy of the PVs. Marker: the vmdk in the VM dir or the absolute path, nfs-data*
# in the machine dir, or the name in disk_meta (config.vm.disk era). The second variable is a deliberate,
# separate act (e.g. the data is already gone).
NFS_DISK_RECORDED = NFS_VMDK_IN_VMDIR || File.exist?(NFS_VMDK_ABS) ||
                    Dir.glob(File.join(NFS_MACHINE_DIR, "disk_meta")).any? { |f| File.read(f).include?("nfs-data") } ||
                    !Dir.glob(File.join(NFS_MACHINE_DIR, "*/nfs-data*")).empty?
if NFS_DISK_GB == 0 && NFS_DISK_RECORDED && ENV["NARWHAL_NFS_DISK_ALLOW_DELETE"] != "yes-delete-the-nfs-disk"
  abort "REFUSING: NARWHAL_NFS_DISK_GB=0 but master-1 already has the NFS data disk (#{NFS_VMDK_NAME}); 0 stops this Vagrantfile from creating/attaching it: " \
        "a new or recreated VM would boot without the disk and export the stale root-disk copy of every PV, and an existing VM's " \
        "vmx may keep or lose the attach depending on its state. Unset NARWHAL_NFS_DISK_GB. Only if the data is already gone: " \
        "NARWHAL_NFS_DISK_ALLOW_DELETE=yes-delete-the-nfs-disk."
end
# "auto": 01-nfs-server.sh picks the single blank non-OS disk (no /dev/sdb guessing; fstab uses the UUID).
NFS_DATA_DEVICE = NFS_DISK_GB > 0 ? "auto" : ""

#=========================================
# Resource Configuration
#=========================================
MASTER_CPUS = 2
MASTER_MEMORY = 6144    # 6GB - control plane + DaemonSets headroom
# D-WORKER-CPUS: 2 vCPU workers ran out of CPU *requests* (97-99% requested, ~15% used) once the
# full platform was up on 2026-11-02: Keycloak (600m) could not schedule and the Falco DaemonSet
# fought it for the last 100m. 4 vCPU gives headroom; 3 masters x 2 + 3 workers x 4 = 18 vCPU, an
# overcommit that is fine on a 14-core host because actual use is low. Override with NARWHAL_WORKER_CPUS.
WORKER_CPUS = Integer(ENV.fetch("NARWHAL_WORKER_CPUS", "4"), exception: false)
abort "NARWHAL_WORKER_CPUS must be a positive integer (got #{ENV['NARWHAL_WORKER_CPUS'].inspect})" unless WORKER_CPUS && WORKER_CPUS >= 1
# D-WORKER-MEMORY (owner decision 2026-11-08): workers 6GB -> 8GB. At 6GB a worker had ~5.3GiB
# allocatable and prometheus/tempo/loki/seaweedfs co-located on one node hit the kernel OOM killer
# (2026-11-06, see lessons-log). Cost: +6GB host RAM for 3 workers (3x6 + 3x8 = 42GB of VMs on a
# 64GiB host); masters stay 6GB. memsize is applied at VM start, so an existing cluster needs a
# halt/up per worker (docs/vagrant/operations.md "Worker memory 8GB"). Escape hatch: set 6144.
WORKER_MEMORY = 8192    # 8GB - platform apps

#=========================================
# Disk Configuration
#=========================================
# Minimum 50GB recommended for IDP full deployment
DISK_SIZE_GB = 30  # GB

Vagrant.configure("2") do |config|
  # Base Box (XFS for project quota support)
  config.vm.box = "dasomel/ubuntu-26.04-xfs"
  config.vm.box_version = "0.3.1"
  # D-BOX-CATALOG: kube-ready-box left Vagrant Cloud (HCP Vagrant stopped taking new versions on
  # 2026-10-01 and deletes hosted boxes on 2026-12-31); 0.3.0+ is served as GitHub Release assets
  # through this catalog, which carries the sha256 so `vagrant box add/update` still verify it.
  # 0.3.0 shipped arm64 vmware_desktop only; the 0.3.1 catalog also lists a virtualbox amd64 build that
  # this repo has never run, so the validated path is vmware_desktop on arm64 (the virtualbox blocks below
  # are unvalidated on 0.3.x). 0.3.1 = 0.3.0 + installer GRUB
  # args removed and leftover installer kernels purged (same kernel 7.0.0-42).
  config.vm.box_url = "https://dasomel.github.io/kube-ready-box/boxes/ubuntu-26.04-xfs.json"
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

  # Airgap bundle: OS packages and Helm charts, mounted rather than copied.
  #
  # AIRGAP=1 switches the provisioning scripts onto these instead of pkgs.k8s.io, the
  # Ubuntu archive and public chart repositories. Mounting keeps the 6.4 GB of images out
  # of the guests — only apt/ (148 MB) and charts/ (4.2 MB) are needed there; images go to
  # the registry via scripts/airgap/04+05 and are pulled by containerd.
  #
  # Guarded on existence so a normal online `vagrant up` still works with no bundle
  # present: a synced_folder pointing at a missing directory is a hard Vagrant error, not
  # a warning.
  airgap_bundle = File.join(__dir__, "narwhal-airgap-bundle-#{AIRGAP_ARCH_SUFFIX}")
  # /srv, not /home/vagrant: apt drops privileges to the `_apt` user before fetching, and
  # /home/vagrant is 0750 vagrant:vagrant, so `_apt` cannot traverse into it. apt reports
  # that as "File not found - /home/vagrant/apt/./Packages" rather than a permission error,
  # which reads like a missing index and sends you looking for the wrong bug — the index
  # (Packages.gz) is there and 0644, the parent directory is what blocks it. /srv is 0755
  # root:root. (/mnt/vagrant-mounts, where Vagrant keeps its own copy of the mount, is 0700
  # and blocks it too.) charts/ stays under /home/vagrant because helm runs as root.
  if Dir.exist?(File.join(airgap_bundle, "apt"))
    config.vm.synced_folder File.join(airgap_bundle, "apt"), "/srv/airgap/apt",
      owner: "vagrant", group: "vagrant"
  end
  if Dir.exist?(File.join(airgap_bundle, "charts"))
    # lib-charts.sh resolves NARWHAL_CHART_DIR here; the Kakao path stages the same
    # tarballs to the same place with scripts/cloud/stage-kakao-nodes.sh.
    config.vm.synced_folder File.join(airgap_bundle, "charts"), "/home/vagrant/charts",
      owner: "vagrant", group: "vagrant"
  end
  # bin/ and manifests/ are the other two directories lib-charts.sh resolves against
  # (NARWHAL_BIN_DIR, NARWHAL_MANIFEST_DIR). They were bundled and staged for the Kakao
  # path but never mounted here, so an offline Vagrant install got as far as the CNI step
  # and stopped at `ERROR: 'helm' is not in /home/vagrant/bin` — the binary was in the
  # bundle the whole time. All three mounts belong together; adding one without the others
  # is what produced that gap.
  if Dir.exist?(File.join(airgap_bundle, "bin"))
    config.vm.synced_folder File.join(airgap_bundle, "bin"), "/home/vagrant/bin",
      owner: "vagrant", group: "vagrant"
  end
  if Dir.exist?(File.join(airgap_bundle, "manifests"))
    config.vm.synced_folder File.join(airgap_bundle, "manifests"), "/home/vagrant/manifests",
      owner: "vagrant", group: "vagrant"
  end

  # Sync the in-repo portal/ so the in-cluster Kaniko build
  # (15-narwhal-portal.sh -> kaniko-build.sh) has the portal source on the VM.
  # rsync excludes build junk so this stays small (a few MB, not the 1.5G tree).
  if Dir.exist?(File.join(__dir__, "portal"))
    config.vm.synced_folder "portal", "/home/vagrant/narwhal-portal",
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
        # D-NFS-VMDK-VMX: second disk for the NFS export, master-1 only (the NFS server); see the constants above.
        # Attach is the vmx keys below; the vmdk itself is created by the trigger after this block.
        if i == 1 && NFS_DISK_GB > 0
          vmware.vmx["nvme0:1.present"] = "TRUE"
          vmware.vmx["nvme0:1.fileName"] = NFS_VMDK_VMX
        end
      end

      if i == 1 && NFS_DISK_GB > 0
        # VirtualBox keeps core Vagrant disk config (vdi). NOT verified (no VirtualBox host); vmware does not use it.
        master.vm.provider "virtualbox" do |_vb, override|
          override.vm.disk :disk, size: "#{NFS_DISK_GB}GB", name: "nfs-data"
        end

        # Create the vmdk before the VM powers on (up and reload both boot it). Idempotent: an existing file is
        # never touched. Runs on the host; ruby block so it needs no shell quoting and works per provider.
        master.trigger.before [:up, :reload] do |trigger|
          trigger.name = "NFS data vmdk"
          trigger.ruby do |_env, machine|
            next unless machine.provider_name == :vmware_desktop
            target = NFS_VMDK_IN_VMDIR ? File.join(NFS_VM_DIR, NFS_VMDK_NAME) : NFS_VMDK_ABS
            next if File.exist?(target)
            tool = ["/Applications/VMware Fusion.app/Contents/Library/vmware-vdiskmanager"].find { |t| File.executable?(t) } ||
                   "vmware-vdiskmanager"
            FileUtils.mkdir_p(File.dirname(target))
            machine.ui.info("Creating #{NFS_DISK_GB}GB growable NFS data disk #{target}")
            ok = system(tool, "-c", "-s", "#{NFS_DISK_GB}GB", "-a", "lsilogic", "-t", "0", target)
            abort "vmware-vdiskmanager failed (#{tool}); cannot create #{target}" unless ok
          end
        end

        # The absolute-path vmdk is outside the VM dir, so destroy would otherwise leave it behind.
        master.trigger.after :destroy do |trigger|
          trigger.name = "NFS data vmdk cleanup"
          trigger.ruby do |_env, machine|
            next unless machine.provider_name == :vmware_desktop
            # Vagrant runs after-triggers even when the user answers N to the destroy prompt: delete only
            # once the machine is really gone. Never touches a vmdk inside the VM dir (destroy owns that).
            next unless machine.state.id == :not_created
            FileUtils.rm_rf(File.dirname(NFS_VMDK_ABS))
          end
        end
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
          "DOMAIN" => BASE_DOMAIN,
          "AIRGAP" => AIRGAP,
          "AIRGAP_REGISTRY" => AIRGAP_REGISTRY
        }
      master.vm.provision "shell", path: "scripts/common/02-containerd.sh",
        env: { "DOMAIN" => BASE_DOMAIN, "AIRGAP" => AIRGAP, "AIRGAP_REGISTRY" => AIRGAP_REGISTRY }
      master.vm.provision "shell", path: "scripts/common/03-k8s-install.sh",
        env: { "K8S_VERSION" => K8S_VERSION, "K8S_PATCH_VERSION" => K8S_PATCH_VERSION, "DOMAIN" => BASE_DOMAIN, "AIRGAP" => AIRGAP }
      master.vm.provision "shell", path: "scripts/common/06-boot-heal-install.sh"

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
            "NFS_DATA_DEVICE" => NFS_DATA_DEVICE,
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
            "DOMAIN" => BASE_DOMAIN,
            "K8S_PATCH_VERSION" => K8S_PATCH_VERSION
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
          "DOMAIN" => BASE_DOMAIN,
          "AIRGAP" => AIRGAP,
          "AIRGAP_REGISTRY" => AIRGAP_REGISTRY
        }
      worker.vm.provision "shell", path: "scripts/common/02-containerd.sh",
        env: { "DOMAIN" => BASE_DOMAIN, "AIRGAP" => AIRGAP, "AIRGAP_REGISTRY" => AIRGAP_REGISTRY }
      worker.vm.provision "shell", path: "scripts/common/03-k8s-install.sh",
        env: { "K8S_VERSION" => K8S_VERSION, "K8S_PATCH_VERSION" => K8S_PATCH_VERSION, "DOMAIN" => BASE_DOMAIN, "AIRGAP" => AIRGAP }
      worker.vm.provision "shell", path: "scripts/common/06-boot-heal-install.sh"

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

      # D7: Phase 2 auto-trigger removed.
      # Previously this fired `vagrant provision master-1 --provision-with phase2-platform`
      # when worker-3's `vagrant up` completed, but worker join order and timing are not
      # guaranteed under up.sh's per-VM resilient bring-up.  In a failed run the trigger
      # fired while another worker had not yet stably joined, causing node flapping during
      # Phase 2 and aborting 08-1's kubectl operations.
      # up.sh is now the single authoritative Phase 2 driver: it polls until ALL
      # EXPECTED_NODES are Ready, then runs phase2-platform exactly once.
      # Do NOT re-add this trigger.
    end
  end
end
