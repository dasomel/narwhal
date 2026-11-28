#!/usr/bin/env python3
"""R423 (lane-c): Control-plane static Pod securityContext patches + CIS 4.2.6 protectKernelDefaults."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
PREREQ = 'scripts/common/01-prerequisites.sh'
SYSCTL_LIB = 'scripts/common/lib-kubelet-kernel-defaults.sh'
SYSCTL_CONF = 'scripts/ops/lib/95-kubelet-kernel-defaults.conf'
INIT = 'scripts/cluster/02-init-cluster.sh'
JOIN_CP = 'scripts/cluster/02-join-control-plane.sh'
JOIN_W = 'scripts/cluster/02-join-worker.sh'
HARDEN = 'scripts/cluster/harden-node-files.sh'
OPS_CP = 'scripts/ops/apply-control-plane-security-context.sh'
HELPER_CP = 'scripts/ops/lib/control-plane-security-context.py'
OPS_KD = 'scripts/ops/apply-kubelet-kernel-defaults.sh'
NODE_KD = 'scripts/ops/lib/kubelet-kernel-defaults-node.sh'
RULES = 'portal/src/data/compliance-acceptance-rules.json'
REGRESSION = 'scripts/test/regression-check-kakao.sh'

COMPONENTS = ('kube-scheduler', 'kube-controller-manager', 'etcd', 'kube-apiserver')
PATCH_FILES = tuple(f'scripts/ops/lib/kubeadm-patches/{c}+strategic.yaml' for c in COMPONENTS)
FILES = (
    PREREQ, SYSCTL_LIB, SYSCTL_CONF, INIT, JOIN_CP, JOIN_W, HARDEN,
    OPS_CP, HELPER_CP, OPS_KD, NODE_KD, RULES, REGRESSION, *PATCH_FILES,
)

EXPECTED_SYSCTLS = {
    'vm.overcommit_memory': '1',
    'vm.panic_on_oom': '0',
    'kernel.panic': '10',
    'kernel.panic_on_oops': '1',
    'kernel.keys.root_maxkeys': '1000000',
    'kernel.keys.root_maxbytes': '25000000',
}


def yq_json(text):
    return json.loads(subprocess.check_output(['yq', '-o=json', '.'], input=text, text=True))


def sample_manifest(comp, extra_sc=''):
    return f'''apiVersion: v1
kind: Pod
metadata:
  name: {comp}
  namespace: kube-system
spec:
  containers:
  - command:
    - {comp}
    - --profiling=false
    image: registry.k8s.io/{comp}:v1.35.7
    name: {comp}
{extra_sc}  hostNetwork: true
'''


def check_static(files):
    # 1. Canonical 4 kubeadm strategic patch files (D-CP-ETCD-DAC: etcd adds DAC_OVERRIDE only)
    for comp in COMPONENTS:
        doc = yq_json(files[f'scripts/ops/lib/kubeadm-patches/{comp}+strategic.yaml'])
        assert doc['apiVersion'] == 'v1' and doc['kind'] == 'Pod'
        assert doc['metadata'] == {'name': comp, 'namespace': 'kube-system'}
        assert doc['spec']['securityContext'] == {'seccompProfile': {'type': 'RuntimeDefault'}}
        expected_caps = {'drop': ['ALL'], 'add': ['DAC_OVERRIDE']} if comp == 'etcd' else {'drop': ['ALL']}
        containers = doc['spec']['containers']
        assert containers == [{
            'name': comp,
            'securityContext': {
                'allowPrivilegeEscalation': False,
                'capabilities': expected_caps,
            },
        }], f'unexpected strategic patch for {comp}: {doc}'

    # 2. Canonical sysctl file + shared lib
    parsed_sysctls = {}
    for line in files[SYSCTL_CONF].splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        k, v = [p.strip() for p in s.split('=', 1)]
        parsed_sysctls[k] = v
    assert parsed_sysctls == EXPECTED_SYSCTLS, f'unexpected sysctl conf: {parsed_sysctls}'
    slib = files[SYSCTL_LIB]
    for k, v in EXPECTED_SYSCTLS.items():
        assert f'"{k}={v}"' in slib, f'missing {k}={v} in {SYSCTL_LIB}'
    assert 'sysctl -p "${sysctl_file}"' in slib and 'sysctl --system' in slib
    assert 'verify_kubelet_kernel_defaults' in slib

    # 3. 01-prerequisites.sh, 02-init-cluster.sh, 02-join-control-plane.sh, 02-join-worker.sh
    for script_path in (PREREQ, INIT, JOIN_CP, JOIN_W):
        txt = files[script_path]
        assert 'lib-kubelet-kernel-defaults.sh' in txt, f'{script_path} missing sysctl lib'
        assert 'ensure_kubelet_kernel_defaults' in txt, f'{script_path} missing ensure_kubelet_kernel_defaults'

    init = files[INIT]
    assert init.index('/etc/kubernetes/patches/${patch_file}') < init.index('sudo kubeadm init ')
    assert init.index('ensure_kubelet_kernel_defaults') < init.index('sudo kubeadm init ')
    raw_cfg = init.split('cat <<EOF > /tmp/kubeadm-config.yaml\n', 1)[1].split('\nEOF\n', 1)[0]
    docs = [yq_json(d) for d in raw_cfg.replace('${CERT_SANS}', '\n    - localhost').split('\n---\n')]
    init_doc = next(d for d in docs if d.get('kind') == 'InitConfiguration')
    kubelet_doc = next(d for d in docs if d.get('kind') == 'KubeletConfiguration')
    assert init_doc.get('patches') == {'directory': '/etc/kubernetes/patches'}, f'InitConfiguration patches mismatch: {init_doc}'
    assert kubelet_doc.get('protectKernelDefaults') is True, f'KubeletConfiguration missing protectKernelDefaults: {kubelet_doc}'

    join_cp = files[JOIN_CP]
    assert join_cp.index('/etc/kubernetes/patches/${patch_file}') < join_cp.index('$(cat /tmp/join-control-plane.sh)')
    assert join_cp.index('ensure_kubelet_kernel_defaults') < join_cp.index('$(cat /tmp/join-control-plane.sh)')
    assert '--patches=/etc/kubernetes/patches' in join_cp

    join_w = files[JOIN_W]
    assert join_w.index('ensure_kubelet_kernel_defaults') < join_w.index('sudo bash /tmp/join-command.sh')

    # 4. harden-node-files.sh keeps etcd:etcd ownership for CIS 1.1.12 / KCV0059 (D-CP-ETCD-DAC)
    harden_active = '\n'.join(l for l in files[HARDEN].splitlines() if l.strip() and not l.lstrip().startswith('#'))
    assert 'chown -R etcd:etcd "${prefix}/var/lib/etcd"' in harden_active
    assert 'ETCD_DIR_OWNER' not in harden_active
    assert 'chown -R root:root "${prefix}/var/lib/etcd"' not in harden_active
    # ops script must not refuse non-root /var/lib/etcd ownership
    assert 'check_etcd_owner' not in files[OPS_CP] and 'check-etcd' not in files[OPS_CP]

    # 5. Portal exact acceptance rules: KSV012/020/021 accepted on all 4, KSV022/106 on etcd only, KSV001/003/004 unaccepted
    rules = json.loads(files[RULES])['configAudit']
    for comp in COMPONENTS:
        pod_name = f'{comp}-narwhal-master-1'
        matched = [r for r in rules if r['namespace'] == 'kube-system' and r['kind'] == 'Pod' and r['name'] == pod_name]
        assert len(matched) == 1, f'missing exact rule for {pod_name}'
        checks = set(matched[0]['checks'])
        if comp == 'etcd':
            assert {'KSV009', 'KSV012', 'KSV020', 'KSV021', 'KSV022', 'KSV023', 'KSV024', 'KSV106'} == checks, f'{pod_name} checks={checks}'
            assert matched[0]['reasonKey'] == 'compliance.acceptedReason.verifiedkubeadmEtcdHost'
            assert not (checks & {'KSV001', 'KSV003', 'KSV004', 'KSV105'}), f'{pod_name} accepted fixed/forbidden check'
        else:
            assert {'KSV009', 'KSV012', 'KSV020', 'KSV021', 'KSV023', 'KSV024'} == checks, f'{pod_name} checks={checks}'
            assert matched[0]['reasonKey'] == 'compliance.acceptedReason.verifiedkubeadmHost'
            assert not (checks & {'KSV001', 'KSV003', 'KSV004', 'KSV022', 'KSV105', 'KSV106'}), f'{pod_name} accepted fixed/forbidden check'

    # 6. Regression registration
    assert 'check R423 ' in files[REGRESSION]
    assert 'check-control-plane-static-pods-and-kubelet-defaults.py --mutation-verify' in files[REGRESSION]


def _install_common_stubs(bindir):
    stat_stub = bindir / 'stat'
    stat_stub.write_text(r'''#!/usr/bin/env python3
import os, sys
if len(sys.argv) == 4 and sys.argv[1] == '-c' and sys.argv[2] == '%d':
    path = sys.argv[3]
    bad = os.environ.get('BAD_DEVICE', '')
    if (bad == 'manifest' and path.endswith('/manifests')) or (bad == 'patch' and path.endswith('/patches')):
        print(999999)
    else:
        print(os.stat(path).st_dev)
    sys.exit(0)
sys.exit(2)
''')
    install_stub = bindir / 'install'
    install_stub.write_text(r'''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args, mode, is_dir, paths, i = sys.argv[1:], None, False, [], 0
while i < len(args):
    if args[i] == '-d': is_dir = True; i += 1
    elif args[i] == '-m': mode = int(args[i + 1], 8); i += 2
    elif args[i] in ('-o', '-g'): i += 2
    else: paths.append(args[i]); i += 1
if is_dir:
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)
        if mode is not None: os.chmod(p, mode)
else:
    src, dst = Path(paths[0]), Path(paths[1])
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    if mode is not None: os.chmod(dst, mode)
''')
    chown_stub = bindir / 'chown'
    chown_stub.write_text('#!/usr/bin/env bash\nexit 0\n')
    for p in (stat_stub, install_stub, chown_stub):
        p.chmod(0o755)


def check_ops_cp(files, scenario='all'):
    with tempfile.TemporaryDirectory(prefix='r423-cp-') as td:
        root = Path(td)
        bindir = root / 'bin'
        bindir.mkdir()
        _install_common_stubs(bindir)

        ssh_stub = bindir / 'ssh'
        ssh_stub.write_text(r'''#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path
a = sys.argv
assert 'BatchMode=yes' in a and 'StrictHostKeyChecking=yes' in a and 'sudo' in a
host = next(v for v in a if '@' in v).split('@')[1]
nroot = Path(os.environ['NODE_ROOT']) / host
def rw(s):
    return (
        s.replace('/etc/kubernetes', str(nroot / 'etc/kubernetes'))
        .replace('/var/lib/narwhal/control-plane-security-context', str(nroot / 'backups'))
    )
if 'cat' in a:
    sys.stdout.write(Path(rw(a[-1])).read_text())
    sys.exit(0)
script_text = rw(sys.stdin.read())
args = [rw(v) for v in a[a.index('--') + 1:]]
res = subprocess.run(['bash', '-s', '--'] + args, input=script_text, text=True, capture_output=True,
                     env={**os.environ, 'CUR_HOST': host})
sys.stdout.write(res.stdout.replace(str(nroot / 'backups'), '/var/lib/narwhal/control-plane-security-context'))
sys.stderr.write(res.stderr)
if res.returncode == 0 and args and args[0] == 'apply' and os.environ.get('CORRUPT_WRITE') == args[1]:
    p = nroot / f'etc/kubernetes/manifests/{args[1]}.yaml'
    p.write_text(p.read_text().replace('--profiling=false', '--profiling=true'))
sys.exit(res.returncode)
''')
        kubectl_stub = bindir / 'kubectl'
        kubectl_stub.write_text(r'''#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys
from pathlib import Path
root = Path(os.environ['NODE_ROOT'])
a = sys.argv
if 'node' in a:
    print(json.dumps({'metadata': {'labels': {'node-role.kubernetes.io/control-plane': ''}}}))
    sys.exit(0)
if '/readyz' in a:
    print('ok'); sys.exit(0)
if 'pod' in a:
    pod_name = a[a.index('pod') + 1]
    comp, host = pod_name.rsplit('-', 1)
    manifest = root / host / f'etc/kubernetes/manifests/{comp}.yaml'
    raw = manifest.read_text()
    doc = json.loads(subprocess.check_output(['yq', '-o=json', '.', str(manifest)], text=True))
    uid = hashlib.sha256(raw.encode()).hexdigest()
    if any('jsonpath' in v for v in a):
        print(uid); sys.exit(0)
    sc = doc['spec']['containers'][0].get('securityContext') or {}
    has_drop = (sc.get('capabilities') or {}).get('drop') == ['ALL']
    ready = 'True'
    if has_drop and os.environ.get('NOT_READY') == f'{host}/{comp}':
        ready = 'False'
    if (not has_drop) and os.environ.get('ROLLBACK_NOT_READY') == f'{host}/{comp}':
        ready = 'False'
    doc['metadata'] = {'name': pod_name, 'uid': uid}
    doc['status'] = {'conditions': [{'type': 'Ready', 'status': ready}]}
    print(json.dumps(doc))
    sys.exit(0)
sys.exit('unexpected kubectl: ' + ' '.join(a))
''')
        for p in (ssh_stub, kubectl_stub):
            p.chmod(0o755)

        ops_dir = root / 'ops'
        (ops_dir / 'lib/kubeadm-patches').mkdir(parents=True)
        ops_copy = ops_dir / 'apply-control-plane-security-context.sh'
        ops_copy.write_text(files[OPS_CP])
        (ops_dir / 'lib/control-plane-security-context.py').write_text(files[HELPER_CP])
        for comp in COMPONENTS:
            (ops_dir / f'lib/kubeadm-patches/{comp}+strategic.yaml').write_text(
                files[f'scripts/ops/lib/kubeadm-patches/{comp}+strategic.yaml']
            )

        for host in ('one', 'two'):
            mdir = root / host / 'etc/kubernetes/manifests'
            mdir.mkdir(parents=True)
            for comp in COMPONENTS:
                (mdir / f'{comp}.yaml').write_text(sample_manifest(comp))

        kc = root / 'admin.conf'
        kc.touch()
        env = {**os.environ, 'PATH': f"{bindir}:{os.environ['PATH']}", 'NODE_ROOT': td}

        def run(nodes=('one=u@one', 'two=u@two'), extra=(), fail=False, ov=None):
            cmd = ['bash', str(ops_copy), '--kubeconfig', str(kc), '--timeout', '1']
            for n in nodes:
                cmd += ['--node', n]
            cmd += list(extra)
            res = subprocess.run(cmd, env=env | (ov or {}), text=True, capture_output=True, timeout=45)
            assert (res.returncode != 0) == fail, f'rc={res.returncode}\nOUT:\n{res.stdout}\nERR:\n{res.stderr}'
            return res

        if scenario in ('all', 'dry'):
            r = run()
            assert 'dry-run complete' in r.stdout and 'allowPrivilegeEscalation: false' in r.stdout and 'DAC_OVERRIDE' in r.stdout, r.stdout
            for h in ('one', 'two'):
                assert not (root / h / 'backups').exists()
                assert not (root / h / 'etc/kubernetes/patches').exists()
                assert (root / h / 'etc/kubernetes/manifests/etcd.yaml').read_text() == sample_manifest('etcd')
            if scenario == 'dry':
                return

        if scenario in ('all', 'refuse'):
            bad_p = root / 'one/etc/kubernetes/manifests/kube-apiserver.yaml'
            bad_p.write_text(sample_manifest('kube-apiserver', '    securityContext:\n      allowPrivilegeEscalation: true\n'))
            run(nodes=('one=u@one',), extra=('--apply',), fail=True)
            bad_p.write_text(sample_manifest('kube-apiserver', '    securityContext:\n      capabilities:\n        add:\n        - DAC_OVERRIDE\n'))
            run(nodes=('one=u@one',), extra=('--apply',), fail=True)
            bad_p.write_text(sample_manifest('kube-apiserver'))
            bad_etcd = root / 'one/etc/kubernetes/manifests/etcd.yaml'
            bad_etcd.write_text(sample_manifest('etcd', '    securityContext:\n      capabilities:\n        add:\n        - SYS_ADMIN\n'))
            run(nodes=('one=u@one',), extra=('--apply',), fail=True)
            bad_etcd.write_text(sample_manifest('etcd'))
            if scenario == 'refuse':
                return

        if scenario in ('all', 'stdev'):
            run(nodes=('one=u@one',), extra=('--apply',), fail=True, ov={'BAD_DEVICE': 'manifest'})
            assert (root / 'one/etc/kubernetes/manifests/kube-scheduler.yaml').read_text() == sample_manifest('kube-scheduler')
            run(nodes=('one=u@one',), extra=('--apply',), fail=True, ov={'BAD_DEVICE': 'patch'})
            assert (root / 'one/etc/kubernetes/manifests/kube-scheduler.yaml').read_text() == sample_manifest('kube-scheduler')
            if scenario == 'stdev':
                return

        if scenario in ('all', 'corrupt'):
            r = run(nodes=('one=u@one',), extra=('--apply',), fail=True, ov={'CORRUPT_WRITE': 'etcd'})
            assert 'changes fields outside securityContext hardening' in r.stderr
            for comp in COMPONENTS:
                assert (root / f'one/etc/kubernetes/manifests/{comp}.yaml').read_text() == sample_manifest(comp)
            if scenario == 'corrupt':
                return

        if scenario in ('all', 'rollback_partial'):
            r = run(extra=('--apply',), fail=True, ov={'NOT_READY': 'two/etcd'})
            assert 'restored and healthy: two/etcd' in r.stderr
            assert 'EARLIER NODE STILL APPLIED: one=u@one' in r.stderr
            for comp in COMPONENTS:
                assert (root / f'two/etc/kubernetes/manifests/{comp}.yaml').read_text() == sample_manifest(comp)
            m = re.search(r'EARLIER NODE STILL APPLIED: one=u@one backup=(/var/lib/narwhal/control-plane-security-context/run\.\S+)', r.stderr)
            assert m, r.stderr
            run(nodes=('one=u@one',), extra=('--rollback', m.group(1)))
            for comp in COMPONENTS:
                assert (root / f'one/etc/kubernetes/manifests/{comp}.yaml').read_text() == sample_manifest(comp)
            if scenario == 'rollback_partial':
                return

        if scenario in ('all', 'rollback_health'):
            r = run(nodes=('one=u@one',), extra=('--apply',), fail=True, ov={'NOT_READY': 'one/etcd', 'ROLLBACK_NOT_READY': 'one/etcd'})
            assert 'ROLLBACK FAILED: one/etcd' in r.stderr
            if scenario == 'rollback_health':
                return

        if scenario in ('all', 'apply'):
            run(extra=('--apply',))
            for h in ('one', 'two'):
                # No backup/temp files leaked inside watched manifests dir.
                assert sorted(p.name for p in (root / h / 'etc/kubernetes/manifests').iterdir()) == sorted(f'{c}.yaml' for c in COMPONENTS)
                for comp in COMPONENTS:
                    doc = yq_json((root / h / f'etc/kubernetes/manifests/{comp}.yaml').read_text())
                    assert doc['spec']['securityContext']['seccompProfile'] == {'type': 'RuntimeDefault'}
                    expected_caps = {'drop': ['ALL'], 'add': ['DAC_OVERRIDE']} if comp == 'etcd' else {'drop': ['ALL']}
                    assert doc['spec']['containers'][0]['securityContext'] == {
                        'allowPrivilegeEscalation': False,
                        'capabilities': expected_caps,
                    }
                    assert (root / h / f'etc/kubernetes/patches/{comp}+strategic.yaml').is_file()
            r_idem = run(extra=('--apply',))
            assert r_idem.stdout.count('backup=none') == 8


def check_ops_kd(files, scenario='all'):
    with tempfile.TemporaryDirectory(prefix='r423-kd-') as td:
        root = Path(td)
        bindir = root / 'bin'
        bindir.mkdir()
        _install_common_stubs(bindir)

        # Stub openssl, sysctl, systemctl, flock, timeout, ssh, kubectl
        (bindir / 'openssl').write_text('#!/usr/bin/env bash\necho "subject=CN=system:node:$CUR_HOST,O=system:nodes"\n')
        (bindir / 'flock').write_text('#!/usr/bin/env bash\nexit 0\n')
        (bindir / 'timeout').write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        (bindir / 'sysctl').write_text(r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
nroot = Path(os.environ['NODE_ROOT']) / os.environ['CUR_HOST']
proc = nroot / 'proc/sys'
conf = nroot / 'etc/sysctl.d/95-kubelet-kernel-defaults.conf'
def apply_file(p):
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith('#'): continue
        k, v = [x.strip() for x in s.split('=', 1)]
        target = proc / k.replace('.', '/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(v + '\n')
if len(sys.argv) >= 3 and sys.argv[1] == '-p':
    apply_file(Path(sys.argv[2]))
elif '--system' in sys.argv:
    if conf.is_file(): apply_file(conf)
    if os.environ.get('SYSCTL_OVERRIDE') == os.environ['CUR_HOST']:
        p = proc / 'kernel/panic'
        p.write_text('0\n')
''')
        (bindir / 'systemctl').write_text(r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
host = os.environ['CUR_HOST']
if 'restart' in sys.argv:
    if os.environ.get('KUBELET_FAIL_RESTART') == host:
        cfg = (Path(os.environ['NODE_ROOT']) / host / 'var/lib/kubelet/config.yaml').read_text()
        if 'protectKernelDefaults: true' in cfg:
            sys.exit(1)
sys.exit(0)
''')
        (bindir / 'ssh').write_text(r'''#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path
a = sys.argv
host = next(v for v in a if '@' in v).split('@')[1]
nroot = Path(os.environ['NODE_ROOT']) / host
script_text = (
    sys.stdin.read()
    .replace('/var/lib/kubelet/config.yaml', str(nroot / 'var/lib/kubelet/config.yaml'))
    .replace('/etc/sysctl.d/95-kubelet-kernel-defaults.conf', str(nroot / 'etc/sysctl.d/95-kubelet-kernel-defaults.conf'))
    .replace('/var/lib/narwhal/kubelet-kernel-defaults', str(nroot / 'backups'))
    .replace('proc_root=/proc/sys', f'proc_root={nroot / "proc/sys"}')
    .replace('/run/lock/narwhal-kubelet-kernel-defaults.lock', str(nroot / 'lock'))
)
args = a[a.index('--') + 1:]
res = subprocess.run(['bash', '-s', '--'] + args, input=script_text, text=True, capture_output=True,
                     env={**os.environ, 'CUR_HOST': host})
sys.stdout.write(res.stdout)
sys.stderr.write(res.stderr)
sys.exit(res.returncode)
''')
        (bindir / 'kubectl').write_text(r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['NODE_ROOT'])
a = sys.argv
cm_path = root / 'kubelet-cm.json'
if 'nodes' in a:
    print(json.dumps({'items': [{'metadata': {'name': h}} for h in ('master-1', 'worker-1')]}))
    sys.exit(0)
if 'wait' in a:
    sys.exit(0)
if 'kubelet-config' in a:
    if 'patch' in a:
        patch = json.loads(Path(a[a.index('--patch-file') + 1]).read_text())
        doc = json.loads(cm_path.read_text())
        assert patch[0] == {'op': 'test', 'path': '/metadata/resourceVersion', 'value': doc['metadata']['resourceVersion']}
        assert patch[1]['op'] == 'replace' and patch[1]['path'] == '/data/kubelet'
        doc['data']['kubelet'] = patch[1]['value']
        doc['metadata']['resourceVersion'] = str(int(doc['metadata']['resourceVersion']) + 1)
        cm_path.write_text(json.dumps(doc))
    print(cm_path.read_text())
    sys.exit(0)
sys.exit('unexpected kubectl: ' + ' '.join(a))
''')
        for p in bindir.iterdir():
            p.chmod(0o755)

        ops_dir = root / 'ops'
        (ops_dir / 'lib').mkdir(parents=True)
        ops_copy = ops_dir / 'apply-kubelet-kernel-defaults.sh'
        ops_copy.write_text(files[OPS_KD])
        (ops_dir / 'lib/kubelet-kernel-defaults-node.sh').write_text(files[NODE_KD])
        (ops_dir / 'lib/95-kubelet-kernel-defaults.conf').write_text(files[SYSCTL_CONF])

        base_kcfg = 'apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\nreadOnlyPort: 0\n'
        for h in ('master-1', 'worker-1'):
            kc_file = root / h / 'var/lib/kubelet/config.yaml'
            kc_file.parent.mkdir(parents=True)
            kc_file.write_text(base_kcfg)
        cm_path = root / 'kubelet-cm.json'
        cm_path.write_text(json.dumps({'metadata': {'resourceVersion': '1'}, 'data': {'kubelet': base_kcfg}}))
        kc = root / 'admin.conf'
        kc.touch()
        env = {**os.environ, 'PATH': f"{bindir}:{os.environ['PATH']}", 'NODE_ROOT': td}

        def run(extra=(), fail=False, ov=None):
            cmd = ['bash', str(ops_copy), '--kubeconfig', str(kc), '--timeout', '1',
                   '--node', 'master-1=u@master-1', '--node', 'worker-1=u@worker-1', *extra]
            res = subprocess.run(cmd, env=env | (ov or {}), text=True, capture_output=True, timeout=45)
            assert (res.returncode != 0) == fail, f'rc={res.returncode}\nOUT:\n{res.stdout}\nERR:\n{res.stderr}'
            return res

        if scenario in ('all', 'dry'):
            r = run()
            assert 'dry-run complete' in r.stdout
            assert not (root / 'master-1/etc/sysctl.d/95-kubelet-kernel-defaults.conf').exists()
            assert json.loads(cm_path.read_text())['metadata']['resourceVersion'] == '1'
            if scenario == 'dry':
                return

        if scenario in ('all', 'sysctl_override'):
            r = run(extra=('--apply',), fail=True, ov={'SYSCTL_OVERRIDE': 'worker-1'})
            assert 'sysctl mismatch: kernel.panic=0' in r.stderr
            # Must abort in Phase 1 before touching kubelet config on master-1!
            assert (root / 'master-1/var/lib/kubelet/config.yaml').read_text() == base_kcfg
            assert json.loads(cm_path.read_text())['metadata']['resourceVersion'] == '1'
            if scenario == 'sysctl_override':
                return

        if scenario in ('all', 'partial_fail'):
            r = run(extra=('--apply',), fail=True, ov={'KUBELET_FAIL_RESTART': 'worker-1'})
            assert 'Restoring worker-1 to protectKernelDefaults=absent' in r.stderr
            assert 'protectKernelDefaults: true' not in (root / 'worker-1/var/lib/kubelet/config.yaml').read_text()
            assert json.loads(cm_path.read_text())['metadata']['resourceVersion'] == '1'
            # Reset master-1 before full apply
            (root / 'master-1/var/lib/kubelet/config.yaml').write_text(base_kcfg)
            if scenario == 'partial_fail':
                return

        if scenario in ('all', 'apply'):
            run(extra=('--apply',))
            for h in ('master-1', 'worker-1'):
                assert (root / h / 'etc/sysctl.d/95-kubelet-kernel-defaults.conf').read_text() == files[SYSCTL_CONF]
                assert 'protectKernelDefaults: true' in (root / h / 'var/lib/kubelet/config.yaml').read_text()
            cm_after = json.loads(cm_path.read_text())
            assert cm_after['metadata']['resourceVersion'] == '2'
            assert 'protectKernelDefaults: true' in cm_after['data']['kubelet']

            # Idempotent re-run: no CM version bump.
            run(extra=('--apply',))
            assert json.loads(cm_path.read_text())['metadata']['resourceVersion'] == '2'

            # Rollback reverts CM and both nodes.
            run(extra=('--rollback',))
            cm_rb = json.loads(cm_path.read_text())
            assert cm_rb['metadata']['resourceVersion'] == '3'
            assert 'protectKernelDefaults: false' in cm_rb['data']['kubelet']
            for h in ('master-1', 'worker-1'):
                assert 'protectKernelDefaults: false' in (root / h / 'var/lib/kubelet/config.yaml').read_text()


def main():
    files = {name: (ROOT / name).read_text() for name in FILES}
    check_static(files)
    check_ops_cp(files, 'all')
    check_ops_kd(files, 'all')

    if '--mutation-verify' in sys.argv:
        mutations = [
            ('drop allowPrivilegeEscalation in etcd patch', PATCH_FILES[2], 'allowPrivilegeEscalation: false', 'allowPrivilegeEscalation: true', check_static),
            ('drop DAC_OVERRIDE in etcd patch', PATCH_FILES[2], '          add:\n            - DAC_OVERRIDE', '', check_static),
            ('drop capabilities.drop in apiserver patch', PATCH_FILES[3], '- ALL', '- NET_RAW', check_static),
            ('add DAC_OVERRIDE to apiserver patch', PATCH_FILES[3], '          drop:\n            - ALL', '          drop:\n            - ALL\n          add:\n            - DAC_OVERRIDE', check_static),
            ('drop seccompProfile in scheduler patch', PATCH_FILES[0], 'type: RuntimeDefault', 'type: Unconfined', check_static),
            ('drop patches.directory in 02-init-cluster.sh', INIT, 'directory: /etc/kubernetes/patches', 'directory: /tmp/disabled', check_static),
            ('drop protectKernelDefaults in 02-init-cluster.sh', INIT, 'protectKernelDefaults: true', 'protectKernelDefaults: false', check_static),
            ('drop --patches in 02-join-control-plane.sh', JOIN_CP, ' --patches=/etc/kubernetes/patches', '', check_static),
            ('drop sysctl guard in 02-join-worker.sh', JOIN_W, 'ensure_kubelet_kernel_defaults', 'true', check_static),
            ('drop sysctl guard in 01-prerequisites.sh', PREREQ, 'ensure_kubelet_kernel_defaults', 'true', check_static),
            ('drift kernel.panic in 95-kubelet-kernel-defaults.conf', SYSCTL_CONF, 'kernel.panic = 10', 'kernel.panic = 0', check_static),
            ('change etcd:etcd to root:root in harden-node-files.sh', HARDEN, 'chown -R etcd:etcd', 'chown -R root:root', check_static),
            ('re-introduce non-root etcd refusal in ops-cp', OPS_CP, 'case "$action" in', 'check_etcd_owner() { return 3; }\ncase "$action" in', check_static),
            ('accept KSV001 on kube-apiserver in portal rules', RULES, '"KSV009",\n        "KSV012",\n        "KSV020",\n        "KSV021",\n        "KSV023",\n        "KSV024"', '"KSV001",\n        "KSV009",\n        "KSV012",\n        "KSV020",\n        "KSV021",\n        "KSV023",\n        "KSV024"', check_static),
            ('accept KSV022 on kube-apiserver in portal rules', RULES, '"KSV009",\n        "KSV012",\n        "KSV020",\n        "KSV021",\n        "KSV023",\n        "KSV024"', '"KSV009",\n        "KSV012",\n        "KSV020",\n        "KSV021",\n        "KSV022",\n        "KSV023",\n        "KSV024"', check_static),
            ('drop R423 registration', REGRESSION, 'check R423 ', 'echo R423 ', check_static),
            ('drop DAC_OVERRIDE from helper-cp etcd render', HELPER_CP, "'etcd': ['DAC_OVERRIDE'],", "'etcd': [],", lambda fs: check_ops_cp(fs, 'apply')),
            ('skip st_dev check in ops-cp', OPS_CP, '[[ -n "$tmp_dev" && "$tmp_dev" == "$man_dev" ]]', ':', lambda fs: check_ops_cp(fs, 'stdev')),
            ('stage temp inside watched manifests dir in ops-cp', OPS_CP, 'tmpdir=/etc/kubernetes/.narwhal-tmp', 'tmpdir=/etc/kubernetes/manifests/.narwhal-tmp', lambda fs: check_ops_cp(fs, 'apply')),
            ('dry-run mutates in ops-cp', OPS_CP, 'if ((APPLY == 0)); then\n  echo "dry-run complete', 'if ((APPLY == 99)); then\n  echo "dry-run complete', lambda fs: check_ops_cp(fs, 'dry')),
            ('skip post-write verify in ops-cp', OPS_CP, 'python3 "$HELPER" verify "$old" "$WORK/$NODE/$comp.written" "$comp"', ':', lambda fs: check_ops_cp(fs, 'corrupt')),
            ('skip rollback health check in ops-cp', OPS_CP, 'if remote restore "$comp" "$NODE_BACKUP_DIR" && wait_health "$comp" "" "$WORK/restore-$comp.yaml"; then', 'if remote restore "$comp" "$NODE_BACKUP_DIR"; then', lambda fs: check_ops_cp(fs, 'rollback_health')),
            ('accept conflicting allowPrivilegeEscalation in helper-cp', HELPER_CP, "assert ape is False, f'conflicting allowPrivilegeEscalation on {component}: {ape}'", "sc['allowPrivilegeEscalation'] = False", lambda fs: check_ops_cp(fs, 'refuse')),
            ('skip sysctl --system check in node-kd', NODE_KD, 'sysctl --system >/dev/null', ':', lambda fs: check_ops_kd(fs, 'sysctl_override')),
            ('dry-run mutates in ops-kd', OPS_KD, '[[ "$apply" == true ]] || { echo "dry-run complete (no remote or cluster writes)"; exit 0; }', ':', lambda fs: check_ops_kd(fs, 'dry')),
            ('skip resourceVersion test in ops-kd CM patch', OPS_KD, '{op:"test",path:"/metadata/resourceVersion",value:.metadata.resourceVersion},', '', lambda fs: check_ops_kd(fs, 'apply')),
        ]
        for name, fname, old, new, runner in mutations:
            assert old in files[fname], f'mutation anchor missing for {name}'
            mutated = {**files, fname: files[fname].replace(old, new, 1)}
            try:
                runner(mutated)
            except (AssertionError, KeyError, IndexError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            raise AssertionError(f'mutation survived: {name}')
        print(f'R423 PASS: {len(mutations)} mutations rejected')

    print('R423 PASS: control-plane static Pod kubeadm patches (D-CP-ETCD-DAC), etcd:etcd preserved, CIS 4.2.6 protectKernelDefaults + sysctl ordering')


if __name__ == '__main__':
    main()
