#!/usr/bin/env python3
"""R327 (D-NFS-VMDK, D-CNPG-RESTART-AMP, D-CNPG-RESOURCES, D-CNPG-APPDB): NFS on its own disk, CNPG tolerant of stalls.

Pins (1) the Vagrantfile attaches a second disk to master-1 only and passes NFS_DATA_DEVICE to
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
    if not re.search(r'if i == 1 && NFS_DISK_GB > 0\n\s+master\.vm\.disk :disk, size: "#\{NFS_DISK_GB\}GB", name: "nfs-data"', vf):
        errors.append('Vagrantfile: master-1 must attach the nfs-data disk (config.vm.disk, i == 1 only)')
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
                   'project -s', 'nfs-xfs-migration.md', 'snapshot save', 'vagrant halt master-1',
                   'NARWHAL_NFS_DISK_GB=0', '롤링 재부팅'):
        if needle not in ops:
            errors.append(f'operations.md: missing {needle!r}')
    return errors


MUTATIONS = {
    'disk attach removed': ('vf', 'master.vm.disk :disk, size: "#{NFS_DISK_GB}GB", name: "nfs-data"', '# no disk'),
    'disk attached to every master': ('vf', 'if i == 1 && NFS_DISK_GB > 0', 'if NFS_DISK_GB > 0'),
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
    'runbook quota restore dropped': ('ops', 'project -s', 'project'),
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
