#!/usr/bin/env python3
"""R327 (D-NFS-VMDK, D-CNPG-RESTART-AMP, D-CNPG-RESOURCES, D-CNPG-APPDB): NFS on its own disk, CNPG tolerant of stalls.

Pins (1) the Vagrantfile attaches a second disk to master-1 only via vmx keys + a vdiskmanager trigger (never config.vm.disk) and passes NFS_DATA_DEVICE to
01-nfs-server.sh, (2) 01-nfs-server.sh resolves "auto" without guessing a device name and still refuses
to mount over live data, (3) narwhal-db.yaml and 07-cnpg.sh both carry the same liveness probe tolerance,
memory limit with requests and no CPU limit, and a bootstrap database that exists ('narwhal'),
(4) the migration runbook and host-load rule exist. --mutation-verify re-runs the checks against copies
with one defect each.
"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    'vf': 'Vagrantfile',
    'nfs': 'scripts/cluster/01-nfs-server.sh',
    'db': 'gitops/resources/narwhal-db.yaml',
    'sh': 'scripts/cluster/07-cnpg.sh',
    'ops': 'docs/vagrant/operations.md',
}
MIN_LIVENESS_SECONDS = 90  # default is 30s (3 x 10s); the incident stalls lasted > 8s repeatedly


def cluster_from_yaml(text):
    docs = [d for d in yaml.safe_load_all(text) if d and d.get('kind') == 'Cluster']
    return docs[0]['spec'] if len(docs) == 1 else None


def cluster_from_script(text):
    m = re.search(r"<<EOF\n(apiVersion: postgresql\.cnpg\.io/v1\nkind: Cluster\n.*?)\nEOF\n", text, re.S)
    if not m:
        return None
    # the heredoc is unquoted: neutralize shell variables so it parses as YAML
    return yaml.safe_load(re.sub(r"\$\{[A-Z_]+\}", "x", m.group(1)))['spec']


def check_cluster(label, spec, errors):
    if spec is None:
        errors.append(f'{label}: Cluster spec not found')
        return
    live = (spec.get('probes') or {}).get('liveness') or {}
    window = live.get('periodSeconds', 10) * live.get('failureThreshold', 3)
    if window < MIN_LIVENESS_SECONDS:
        errors.append(f'{label}: liveness tolerates {window}s, need >= {MIN_LIVENESS_SECONDS}s (probes.liveness)')
    if live.get('timeoutSeconds', 5) < 10:
        errors.append(f'{label}: probes.liveness.timeoutSeconds < 10')
    iso = live.get('isolationCheck') or {}
    if iso.get('requestTimeout', 1000) < 5000 or iso.get('connectionTimeout', 1000) < 5000:
        errors.append(f'{label}: probes.liveness.isolationCheck timeouts < 5000ms')
    if 'readiness' in (spec.get('probes') or {}):
        errors.append(f'{label}: readiness probe must stay default (fast)')
    res = spec.get('resources') or {}
    if not (res.get('requests') or {}).get('memory') or not (res.get('requests') or {}).get('cpu'):
        errors.append(f'{label}: resources.requests memory and cpu required')
    if not (res.get('limits') or {}).get('memory'):
        errors.append(f'{label}: resources.limits.memory required')
    if 'cpu' in (res.get('limits') or {}):
        errors.append(f'{label}: resources.limits.cpu must stay unset (CFS throttling delays /healthz)')
    init = (spec.get('bootstrap') or {}).get('initdb') or {}
    if init.get('database') != 'narwhal' or init.get('owner') != 'narwhal':
        errors.append(f'{label}: bootstrap.initdb database/owner must be narwhal (default monitoring queries target it)')


def check(t):
    errors = []
    vf = t['vf']
    if not re.search(r'NFS_DISK_GB\s*=\s*Integer\(ENV\.fetch\("NARWHAL_NFS_DISK_GB",\s*"\d+"\)', vf):
        errors.append('Vagrantfile: NFS_DISK_GB constant missing')
    if re.search(r'^\s*\w+\.vm\.disk :disk[^\n]*nvme', vf, re.M) or re.search(r'master\.vm\.disk\b', vf):
        errors.append('Vagrantfile: config.vm.disk on master must not return (vagrant-vmware-desktop 3.0.5 get_disk crashes: File.extname(nil))')
    vmw = re.search(r'master\.vm\.provider "vmware_desktop" do \|vmware\|\n(.*?)\n      end\n', vf, re.S)
    body = vmw.group(1) if vmw else ''
    if not re.search(r'if i == 1 && NFS_DISK_GB > 0\n(?:\s*#.*\n)*\s+vmware\.vmx\["nvme0:1\.present"\] = "TRUE"\n\s+vmware\.vmx\["nvme0:1\.fileName"\] = NFS_VMDK_VMX\n', body):
        errors.append('Vagrantfile: master-1 vmware block must set nvme0:1.present=TRUE and nvme0:1.fileName (i == 1 only)')
    if 'NFS_VMDK_NAME = "narwhal-nfs.vmdk"' not in vf:
        errors.append('Vagrantfile: vmdk name must stay narwhal-nfs.vmdk (live vmx references it)')
    if not re.search(r'master\.trigger\.before \[:up, :reload\] do', vf) \
            or not re.search(r'system\(tool, "-c", "-s", "#\{NFS_DISK_GB\}GB", "-a", "lsilogic", "-t", "0", target\)', vf) \
            or 'next if File.exist?(target)' not in vf:
        errors.append('Vagrantfile: before up/reload trigger creating the vmdk with vmware-vdiskmanager (idempotent) is missing')
    if 'NFS_VM_DIR' not in vf or 'File.dirname(File.read(id_file).strip)' not in vf:
        errors.append('Vagrantfile: VM dir must be resolved from .vagrant/machines/master-1/vmware_desktop/id')
    if not re.search(r'if NFS_DISK_GB == 0 && NFS_DISK_RECORDED && ENV\["NARWHAL_NFS_DISK_ALLOW_DELETE"\] != "yes-delete-the-nfs-disk"\n\s+abort', vf) \
            or not re.search(r'NFS_DISK_RECORDED = NFS_VMDK_IN_VMDIR \|\| File\.exist\?\(NFS_VMDK_ABS\)', vf) \
            or 'disk_meta' not in vf or 'nfs-data*' not in vf:
        errors.append('Vagrantfile: guard refusing NARWHAL_NFS_DISK_GB=0 when the narwhal-nfs.vmdk exists is missing (boot would export the stale root copy)')
    prov = re.search(r'scripts/cluster/01-nfs-server\.sh",\n\s+env: \{(.*?)\}', vf, re.S)
    if not prov or '"NFS_DATA_DEVICE" => NFS_DATA_DEVICE' not in prov.group(1):
        errors.append('Vagrantfile: 01-nfs-server.sh must receive NFS_DATA_DEVICE')
    if not re.search(r'NFS_DATA_DEVICE = NFS_DISK_GB > 0 \? "auto" : ""', vf):
        errors.append('Vagrantfile: NFS_DATA_DEVICE must be "auto" when the disk is attached')
    nfs = t['nfs']
    if '"${NFS_DATA_DEVICE}" = "auto"' not in nfs or 'lsblk -dnpr' not in nfs:
        errors.append('01-nfs-server.sh: "auto" resolution missing')
    if '-ne 1' not in nfs:
        errors.append('01-nfs-server.sh: auto must require exactly one candidate disk')
    if 'Refusing to mount over live data' not in nfs:
        errors.append('01-nfs-server.sh: refusal to mount over a non-empty share path is gone')
    if re.search(r'NFS_DATA_DEVICE="?/dev/sd', nfs):
        errors.append('01-nfs-server.sh: hard-coded /dev/sdX')
    check_cluster('narwhal-db.yaml', cluster_from_yaml(t['db']), errors)
    check_cluster('07-cnpg.sh', cluster_from_script(t['sh']), errors)
    ops = t['ops']
    for needle in ('## 15. NFS export 전용 디스크 (D-NFS-VMDK)', '## 16. 호스트 부하 규칙',
                   'project -s -p ${path} ${id}', 'nfs-xfs-migration.md', 'snapshot save', 'vagrant halt master-1',
                   'NARWHAL_NFS_DISK_GB=0', '롤링 재부팅', 'vmware-vdiskmanager" -c -s', 'nvme0n1', '`/dev/nvme0n2`로 밀린다', 'nvme0:1.fileName',
                   'sudo test -d "$path"', '--type json', '부팅 때 자동으로 시작', 'NARWHAL_NFS_DISK_ALLOW_DELETE', '(`/export/...`)'):
        if needle not in ops:
            errors.append(f'operations.md: missing {needle!r}')
    if "project -s' /srv/nfs/k8s" in ops or re.search(r'xfs_quota -x -c .project -s. ', ops):
        errors.append('operations.md: bare xfs_quota project -s (host /etc/projects holds /export paths) must not return')
    for key in ('db', 'sh'):
        if re.search(r'ArgoCD owns the live Cluster', t[key]):
            errors.append(f'{key}: wrong claim that ArgoCD owns the live narwhal-db Cluster')
    return errors


MUTATIONS = {
    'vmx attach key removed': ('vf', 'vmware.vmx["nvme0:1.present"] = "TRUE"', '# no attach'),
    'vmx fileName key removed': ('vf', 'vmware.vmx["nvme0:1.fileName"] = NFS_VMDK_VMX', '# no filename'),
    'config.vm.disk reintroduced on vmware': ('vf', '      master.vm.provider "virtualbox" do |vb|', '      master.vm.disk :disk, size: "200GB", name: "nfs-data", vmware_desktop__bus_type: "nvme"\n      master.vm.provider "virtualbox" do |vb|'),
    'attach for every master': ('vf', 'if i == 1 && NFS_DISK_GB > 0\n          vmware.vmx', 'if NFS_DISK_GB > 0\n          vmware.vmx'),
    'vmdk renamed': ('vf', 'NFS_VMDK_NAME = "narwhal-nfs.vmdk"', 'NFS_VMDK_NAME = "nfs-data.vmdk"'),
    'create trigger removed': ('vf', 'master.trigger.before [:up, :reload] do', 'master.trigger.before [:destroy] do'),
    'create not idempotent': ('vf', 'next if File.exist?(target)', 'next if false'),
    'vdiskmanager type changed': ('vf', '"-a", "lsilogic", "-t", "0", target)', '"-a", "lsilogic", target)'),
    'vm dir not from id file': ('vf', 'File.dirname(File.read(id_file).strip)', 'nil'),
    'NFS_DATA_DEVICE not passed': ('vf', '"NFS_DATA_DEVICE" => NFS_DATA_DEVICE,', ''),
    'device guess /dev/sdb': ('vf', 'NFS_DATA_DEVICE = NFS_DISK_GB > 0 ? "auto" : ""', 'NFS_DATA_DEVICE = "/dev/sdb"'),
    'auto accepts several disks': ('nfs', '-ne 1', '-lt 1'),
    'refusal removed': ('nfs', 'Refusing to mount over live data', 'mounting'),
    'db liveness block removed': ('db', 'failureThreshold: 12', 'failureThreshold: 3'),
    'db isolation check back to 1s': ('db', 'requestTimeout: 5000', 'requestTimeout: 1000'),
    'db memory limit removed': ('db', '      memory: "1Gi"\n', ''),
    'db cpu limit added': ('db', '      memory: "1Gi"\n', '      memory: "1Gi"\n      cpu: "1"\n'),
    'db bootstrap authentik': ('db', 'database: narwhal', 'database: authentik'),
    'db readiness overridden': ('db', '  probes:\n    liveness:', '  probes:\n    readiness:\n      failureThreshold: 30\n    liveness:'),
    'script liveness shortened': ('sh', 'failureThreshold: 12', 'failureThreshold: 3'),
    'script cpu limit back': ('sh', '      memory: "1Gi"\n', '      memory: "1Gi"\n      cpu: "1"\n'),
    'script requests removed': ('sh', '      cpu: "250m"\n', ''),
    'runbook quota restore dropped': ('ops', 'project -s -p', 'project -p'),
    'runbook bare project -s returns': ('ops', 'head -3 /etc/projects', "sudo xfs_quota -x -c 'project -s' /srv/nfs/k8s\nhead -3 /etc/projects"),
    'runbook loses /export mapping': ('ops', '(`/export/...`)', '(paths)'),
    'delete guard removed': ('vf', 'if NFS_DISK_GB == 0 && NFS_DISK_RECORDED', 'if false && NFS_DISK_RECORDED'),
    'guard ignores the vmdk file': ('vf', 'NFS_DISK_RECORDED = NFS_VMDK_IN_VMDIR || File.exist?(NFS_VMDK_ABS) ||', 'NFS_DISK_RECORDED = false ||'),
    'runbook loses sudo test -d': ('ops', 'sudo test -d "$path"', 'test -d "$path"'),
    'runbook loses nfs-server autostart note': ('ops', '부팅 때 자동으로 시작', '부팅 때 시작'),
    'runbook loses json patch': ('ops', '--type json', '--type merge'),
    'runbook loses nvme0n2': ('ops', '`/dev/nvme0n2`로 밀린다', '`/dev/sdb`로 밀린다'),
    'delete guard override widened': ('vf', 'ENV["NARWHAL_NFS_DISK_ALLOW_DELETE"] != "yes-delete-the-nfs-disk"', 'false'),
    'wrong ArgoCD claim returns': ('sh', 'applied to the live Cluster by 14-gitops-bootstrap.sh; no Argo Application owns it', 'ArgoCD owns the live Cluster'),
    'host-load rule dropped': ('ops', '## 16. 호스트 부하 규칙', '## 16. 기타'),
}


def main():
    t = {k: (ROOT / p).read_text(encoding='utf-8') for k, p in FILES.items()}
    errors = check(t)
    if errors:
        print('\n'.join('FAIL: ' + e for e in errors))
        return 1
    print('PASS: R327 NFS data disk, CNPG probes/resources/bootstrap db')
    if '--mutation-verify' in sys.argv:
        for name, (key, old, new) in MUTATIONS.items():
            if old not in t[key]:
                print(f'FAIL: mutation anchor missing: {name}')
                return 1
            m = dict(t)
            m[key] = t[key].replace(old, new, 1)
            if not check(m):
                print(f'FAIL: mutation not rejected: {name}')
                return 1
        print(f'PASS: {len(MUTATIONS)} mutations rejected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
