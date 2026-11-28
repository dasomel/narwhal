#!/usr/bin/env python3
"""R426 (lane-c): Control-plane static Pod securityContext patches (etcd deferred) + CIS 4.2.6 protectKernelDefaults."""
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
I18N = 'portal/src/lib/i18n.ts'
REGRESSION = 'scripts/test/regression-check-kakao.sh'

COMPONENTS = ('kube-scheduler', 'kube-controller-manager', 'kube-apiserver')
ALL_STATIC_PODS = ('etcd', *COMPONENTS)
PATCH_FILES = tuple(f'scripts/ops/lib/kubeadm-patches/{c}+strategic.yaml' for c in COMPONENTS)
FILES = (
    PREREQ, SYSCTL_LIB, SYSCTL_CONF, INIT, JOIN_CP, JOIN_W, HARDEN,
    OPS_CP, HELPER_CP, OPS_KD, NODE_KD, RULES, I18N, REGRESSION, *PATCH_FILES,
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
    # 1. Canonical 3 kubeadm strategic patch files only; etcd patch must NOT exist (D-CP-ETCD-DEFER)
    assert not (ROOT / 'scripts/ops/lib/kubeadm-patches/etcd+strategic.yaml').exists(), (
        'etcd+strategic.yaml must not exist under D-CP-ETCD-DEFER'
    )
    for comp in COMPONENTS:
        doc = yq_json(files[f'scripts/ops/lib/kubeadm-patches/{comp}+strategic.yaml'])
        assert doc['apiVersion'] == 'v1' and doc['kind'] == 'Pod'
        assert doc['metadata'] == {'name': comp, 'namespace': 'kube-system'}
        assert doc['spec']['securityContext'] == {'seccompProfile': {'type': 'RuntimeDefault'}}
        containers = doc['spec']['containers']
        assert containers == [{
            'name': comp,
            'securityContext': {
                'allowPrivilegeEscalation': False,
                'capabilities': {
                    'drop': ['ALL'],
                    **({'add': ['NET_BIND_SERVICE']} if comp == 'kube-apiserver' else {}),
                },
            },
        }], f'unexpected strategic patch for {comp}: {doc}'

    rules = json.loads(files[RULES])['configAudit']
    etcd_rule = next(r for r in rules if r.get('name') == 'etcd-narwhal-master-1' and r.get('kind') == 'Pod')
    assert {'KSV001', 'KSV003', 'KSV004'} <= set(etcd_rule['checks'])
    assert etcd_rule['reasonKey'] == 'compliance.acceptedReason.etcdStaticPodTrialFailed'
    assert 'etcd hardening patch prevented etcd from starting in the 2026-11-28 live trial; root cause under investigation' in files[I18N]
    assert 'D-CP-APISERVER-FILECAP' in files[PATCH_FILES[2]]

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
    assert 'etcd+strategic.yaml' not in init, '02-init-cluster.sh must not stage etcd+strategic.yaml'
    assert init.index('/etc/kubernetes/patches/${patch_file}') < init.index('sudo kubeadm init ')
    assert init.index('ensure_kubelet_kernel_defaults') < init.index('sudo kubeadm init ')
    raw_cfg = init.split('cat <<EOF > /tmp/kubeadm-config.yaml\n', 1)[1].split('\nEOF\n', 1)[0]
    docs = [yq_json(d) for d in raw_cfg.replace('${CERT_SANS}', '\n    - localhost').split('\n---\n')]
    init_doc = next(d for d in docs if d.get('kind') == 'InitConfiguration')
    kubelet_doc = next(d for d in docs if d.get('kind') == 'KubeletConfiguration')
    assert init_doc.get('patches') == {'directory': '/etc/kubernetes/patches'}, f'InitConfiguration patches mismatch: {init_doc}'
    assert kubelet_doc.get('protectKernelDefaults') is True, f'KubeletConfiguration missing protectKernelDefaults: {kubelet_doc}'

    join_cp = files[JOIN_CP]
    assert 'etcd+strategic.yaml' not in join_cp, '02-join-control-plane.sh must not stage etcd+strategic.yaml'
    assert join_cp.index('/etc/kubernetes/patches/${patch_file}') < join_cp.index('$(cat /tmp/join-control-plane.sh)')
    assert join_cp.index('ensure_kubelet_kernel_defaults') < join_cp.index('$(cat /tmp/join-control-plane.sh)')
    assert '--patches=/etc/kubernetes/patches' in join_cp

    join_w = files[JOIN_W]
    assert join_w.index('ensure_kubelet_kernel_defaults') < join_w.index('sudo bash /tmp/join-command.sh')

    # 4. harden-node-files.sh keeps etcd:etcd ownership for CIS 1.1.12 / KCV0059; ops-cp excludes etcd
    harden_active = '\n'.join(l for l in files[HARDEN].splitlines() if l.strip() and not l.lstrip().startswith('#'))
    assert 'chown -R etcd:etcd "${prefix}/var/lib/etcd"' in harden_active
    assert 'ETCD_DIR_OWNER' not in harden_active
    assert 'chown -R root:root "${prefix}/var/lib/etcd"' not in harden_active

    ops_cp_active = '\n'.join(l for l in files[OPS_CP].splitlines() if l.strip() and not l.lstrip().startswith('#'))
    assert 'ALL_COMPONENTS=(kube-scheduler kube-controller-manager kube-apiserver)' in ops_cp_active
    assert 'etcd' not in ops_cp_active, 'apply-control-plane-security-context.sh active code must not touch etcd'
    helper_cp_active = '\n'.join(l for l in files[HELPER_CP].splitlines() if l.strip() and not l.lstrip().startswith('#'))
    assert 'DAC_OVERRIDE' not in helper_cp_active, 'control-plane-security-context.py active code must not add DAC_OVERRIDE'
    assert "COMPONENTS = ('kube-scheduler', 'kube-controller-manager', 'kube-apiserver')" in helper_cp_active
    # D-CP-APISERVER-FILECAP parity: renderer ADD_CAPS must equal the patch files' capabilities.add.
    assert "ADD_CAPS = {'kube-apiserver': ['NET_BIND_SERVICE']}" in helper_cp_active, 'renderer ADD_CAPS must keep NET_BIND_SERVICE for kube-apiserver only'

    # 5. Portal exact acceptance rules: only etcd accepts KSV001/003/004 while its live failure is investigated.
    rules = json.loads(files[RULES])['configAudit']
    assert 'verifiedkubeadmEtcdHost' not in files[RULES], 'dropped verifiedkubeadmEtcdHost still in rules JSON'
    assert 'verifiedkubeadmEtcdHost' not in files[I18N], 'dropped verifiedkubeadmEtcdHost still in i18n.ts'
    for comp in ALL_STATIC_PODS:
        pod_name = f'{comp}-narwhal-master-1'
        matched = [r for r in rules if r['namespace'] == 'kube-system' and r['kind'] == 'Pod' and r['name'] == pod_name]
        if comp == 'kube-apiserver':
            matched = [r for r in matched if r.get('id', '').endswith(':verified')]
        assert len(matched) == 1, f'missing exact rule for {pod_name}'
        checks = set(matched[0]['checks'])
        expected_checks = {'KSV009', 'KSV012', 'KSV020', 'KSV021', 'KSV023', 'KSV024'}
        if comp == 'etcd':
            expected_checks |= {'KSV001', 'KSV003', 'KSV004'}
            expected_reason = 'compliance.acceptedReason.etcdStaticPodTrialFailed'
        else:
            expected_reason = 'compliance.acceptedReason.verifiedkubeadmHost'
        assert expected_checks == checks, f'{pod_name} checks={checks}'
        assert matched[0]['reasonKey'] == expected_reason
        forbidden = {'KSV105', 'KSV106'} | (set() if comp == 'etcd' else {'KSV001', 'KSV003', 'KSV004'})
        if comp != 'kube-apiserver':
            forbidden.add('KSV022')
        assert not (checks & forbidden), (
            f'{pod_name} accepted fixed/deferred/forbidden check'
        )

    # D-CP-APISERVER-FILECAP: KSV022 is isolated from the broader verified rule.
    apiserver_filecap = [r for r in rules if r.get('id') ==
                         'configAudit:kube-system:Pod:kube-apiserver-narwhal-master-1:net-bind-filecap']
    assert len(apiserver_filecap) == 1, 'missing dedicated kube-apiserver NET_BIND_SERVICE KSV022 rule'
    filecap_rule = apiserver_filecap[0]
    assert (filecap_rule['scope'], filecap_rule['namespace'], filecap_rule['kind'], filecap_rule['name'],
            filecap_rule['checks'], filecap_rule['class'], filecap_rule['reasonKey']) == (
                'namespace', 'kube-system', 'Pod', 'kube-apiserver-narwhal-master-1', ['KSV022'],
                'accept', 'compliance.acceptedReason.apiserverNetBindFileCap')
    assert 'cap_net_bind_service=ep' in files[I18N]
    assert 'exec with EPERM' in files[I18N]
    assert 'CapEff measured 0x400' in files[I18N]

    # 6. Regression registration
    assert 'check R426 ' in files[REGRESSION]
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
    with tempfile.TemporaryDirectory(prefix='r426-cp-') as td:
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
events = Path(os.environ['NODE_ROOT']) / 'events.log'
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
if args and args[0] == 'restore':
    with events.open('a') as ef:
        ef.write(f'restore:{host}:{args[1]}\n')
res = subprocess.run(['bash', '-s', '--'] + args, input=script_text, text=True, capture_output=True,
                     env={**os.environ, 'CUR_HOST': host})
sys.stdout.write(res.stdout.replace(str(nroot / 'backups'), '/var/lib/narwhal/control-plane-security-context'))
sys.stderr.write(res.stderr)
if res.returncode == 0 and args and args[0] == 'apply' and os.environ.get('CORRUPT_WRITE') == args[1]:
    p = nroot / f'etc/kubernetes/manifests/{args[1]}.yaml'
    p.write_text(p.read_text().replace('--profiling=false', '--profiling=true'))
sys.exit(res.returncode)
''')
        crictl_stub = bindir / 'crictl'
        crictl_stub.write_text(r'''#!/usr/bin/env python3
import datetime, hashlib, json, os, subprocess, sys
from pathlib import Path

host = os.environ['CUR_HOST']
nroot = Path(os.environ['NODE_ROOT']) / host
events = Path(os.environ['NODE_ROOT']) / 'events.log'
a = sys.argv[1:]

def manifest_info(comp):
    m = nroot / f'etc/kubernetes/manifests/{comp}.yaml'
    raw = m.read_text()
    doc = json.loads(subprocess.check_output(['yq', '-o=json', '.', str(m)], text=True))
    cid = f'{comp}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}'
    hfile = nroot / f'.cid-hist-{comp}.json'
    hist = json.loads(hfile.read_text()) if hfile.is_file() else []
    if not hist or hist[0] != cid:
        hist.insert(0, cid)
        hfile.write_text(json.dumps(hist))
    return cid, hist, doc

def find_by_cid(cid):
    for comp in ('kube-scheduler', 'kube-controller-manager', 'kube-apiserver', 'etcd'):
        hfile = nroot / f'.cid-hist-{comp}.json'
        if hfile.is_file() and cid in json.loads(hfile.read_text()):
            cur_cid, _, doc = manifest_info(comp)
            return comp, cur_cid, doc
    sys.exit(f'unknown cid: {cid}')

if a[0] == 'ps':
    name_arg = a[a.index('--name') + 1]
    assert name_arg.startswith('^') and name_arg.endswith('$'), f'non-exact --name: {name_arg}'
    comp = name_arg[1:-1]
    cid, hist, doc = manifest_info(comp)
    sc = doc['spec']['containers'][0].get('securityContext') or {}
    has_drop = (sc.get('capabilities') or {}).get('drop') == ['ALL']
    is_all = '-a' in a
    is_quiet = '-q' in a
    running_only = '--state' in a and a[a.index('--state') + 1] == 'running'
    not_running = (
        (has_drop and os.environ.get('CRICTL_NOT_RUNNING') == f'{host}/{comp}')
        or ((not has_drop) and os.environ.get('ROLLBACK_NOT_READY') == f'{host}/{comp}')
    )
    if running_only:
        if not not_running:
            print(cid)
    elif is_all:
        if is_quiet:
            for h in hist:
                print(h)
        else:
            for idx, h in enumerate(hist):
                st = 'Exited' if (idx > 0 or not_running) else 'Running'
                print(f'{h} {comp} {st}')
    sys.exit(0)

if a[0] == 'inspect':
    cid = a[1]
    comp, cur_cid, doc = find_by_cid(cid)
    with events.open('a') as ef:
        ef.write(f'crictl-inspect:{host}:{comp}:{cid}\n')
    c = doc['spec']['containers'][0]
    sc = c.get('securityContext') or {}
    pod_sc = doc['spec'].get('securityContext') or {}
    has_drop = (sc.get('capabilities') or {}).get('drop') == ['ALL']
    bad_sc = has_drop and os.environ.get('BAD_RUNTIME_SC') == f'{host}/{comp}'
    not_running = (
        (has_drop and os.environ.get('CRICTL_NOT_RUNNING') == f'{host}/{comp}')
        or ((not has_drop) and os.environ.get('ROLLBACK_NOT_READY') == f'{host}/{comp}')
    )
    state = 'CONTAINER_RUNNING' if (cid == cur_cid and not not_running) else 'CONTAINER_EXITED'
    if has_drop and not bad_sc:
        kept = ['CAP_' + str(x).upper() for x in ((sc.get('capabilities') or {}).get('add') or [])]
        runtime_spec = {
            'process': {
                'args': list(c.get('command') or []) + list(c.get('args') or []),
                'noNewPrivileges': True,
                'capabilities': {'bounding': list(kept), 'effective': list(kept), 'inheritable': [], 'permitted': list(kept), 'ambient': []},
            },
            'linux': {'seccomp': {'defaultAction': 'SCMP_ACT_ERRNO', 'syscalls': []}},
        }
        cri_sc = {
            'no_new_privs': True,
            'capabilities': {'drop_capabilities': ['ALL'], 'add_capabilities': [x[4:] for x in kept]},
            'seccomp': {'profile_type': 'RuntimeDefault'},
        }
    else:
        runtime_spec = {
            'process': {
                'args': list(c.get('command') or []) + list(c.get('args') or []),
                'noNewPrivileges': False,
                'capabilities': {'bounding': ['CAP_CHOWN', 'CAP_NET_RAW'], 'effective': ['CAP_CHOWN']},
            },
            'linux': {},
        }
        cri_sc = {
            'no_new_privs': False,
            'capabilities': {'drop_capabilities': [], 'add_capabilities': []},
            'seccomp': {'profile_type': 'Unconfined'},
        }
    out = {
        'status': {'id': cid, 'state': state, 'metadata': {'name': comp}},
        'info': {'runtimeSpec': runtime_spec, 'config': {'linux': {'security_context': cri_sc}}},
    }
    print(json.dumps(out))
    sys.exit(0)

if a[0] == 'logs':
    assert '--tail' in a and a[a.index('--tail') + 1] == '300', f'expected --tail 300: {a}'
    cid = a[-1]
    comp, _, _ = find_by_cid(cid)
    with events.open('a') as ef:
        ef.write(f'crictl-logs:{host}:{comp}:{cid}\n')
    print(f'stub-log host={host} comp={comp} cid={cid}')
    sys.exit(0)

sys.exit('unexpected crictl: ' + ' '.join(a))
''')
        ctr_stub = bindir / 'ctr'
        ctr_stub.write_text(r'''#!/usr/bin/env python3
import datetime, hashlib, json, os, subprocess, sys
from pathlib import Path
host=os.environ['CUR_HOST']; root=Path(os.environ['NODE_ROOT'])/host; a=sys.argv[1:]
assert a[:2] == ['-n','k8s.io'], a
if a[2:4] == ['task','ls']:
    for p in sorted((root/'etc/kubernetes/manifests').glob('kube-*.yaml')):
        comp=p.stem; raw=p.read_text(); cid=f'{comp}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}'
        print(f'{cid} 123 RUNNING')
elif a[2:4] == ['containers','ls']:
    comp=a[-1].split('==')[-1]
    p=root/f'etc/kubernetes/manifests/{comp}.yaml'; raw=p.read_text()
    print(f'{comp}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}')
elif a[2:4] == ['containers','info']:
    cid=a[4]; comp=cid.rsplit('-',1)[0]; p=root/f'etc/kubernetes/manifests/{comp}.yaml'
    doc=json.loads(subprocess.check_output(['yq','-o=json','.',str(p)],text=True)); c=doc['spec']['containers'][0]
    created=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=10)).isoformat().replace('+00:00','123Z')  # containerd nanosecond RFC 3339
    sc=c.get('securityContext') or {}; caps=sc.get('capabilities') or {}
    if caps.get('drop') == ['ALL']:
        kept=['CAP_'+str(x).upper() for x in (caps.get('add') or [])]
    else:
        kept=['CAP_CHOWN','CAP_NET_BIND_SERVICE','CAP_NET_RAW']  # runtime default set (unhardened original)
    nnp = sc.get('allowPrivilegeEscalation') is False
    print(json.dumps({'CreatedAt':created,'Spec':{'process':{'noNewPrivileges':nnp,'capabilities':{'bounding':kept,'effective':kept,'permitted':kept}},'linux':{'seccomp':{'defaultAction':'SCMP_ACT_ERRNO'}}}}))
else: sys.exit('unexpected ctr: '+' '.join(a))
''')
        curl_stub = bindir / 'curl'
        curl_stub.write_text(r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path

host = os.environ['CUR_HOST']
nroot = Path(os.environ['NODE_ROOT']) / host
a = sys.argv[1:]
url = next((v for v in a if v.startswith('https://')), '')
assert any('k' in v and v.startswith('-') for v in a), f'curl must pass -k: {a}'
url_map = {
    'https://127.0.0.1:6443/readyz': 'kube-apiserver',
    'https://127.0.0.1:10257/healthz': 'kube-controller-manager',
    'https://127.0.0.1:10259/healthz': 'kube-scheduler',
}
assert url in url_map, f'unexpected local health url: {url}'
comp = url_map[url]
m = nroot / f'etc/kubernetes/manifests/{comp}.yaml'
doc = json.loads(subprocess.check_output(['yq', '-o=json', '.', str(m)], text=True))
sc = doc['spec']['containers'][0].get('securityContext') or {}
has_drop = (sc.get('capabilities') or {}).get('drop') == ['ALL']
if has_drop and os.environ.get('HEALTHZ_FAIL') == f'{host}/{comp}':
    sys.exit(22)
if (not has_drop) and os.environ.get('ROLLBACK_NOT_READY') == f'{host}/{comp}':
    sys.exit(22)
print('ok')
sys.exit(0)
''')
        kubectl_stub = bindir / 'kubectl'
        kubectl_stub.write_text(r'''#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys
from pathlib import Path
if os.environ.get('API_DOWN') == '1':
    sys.exit(1)
root = Path(os.environ['NODE_ROOT'])
a = sys.argv
if 'node' in a:
    print(json.dumps({'metadata': {'labels': {'node-role.kubernetes.io/control-plane': ''}}}))
    sys.exit(0)
if os.environ.get('API_MIRROR_FAIL') == '1':
    sys.exit(1)
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
    doc['metadata'] = {'name': pod_name, 'uid': uid}
    doc['status'] = {'conditions': [{'type': 'Ready', 'status': 'True'}]}
    print(json.dumps(doc))
    sys.exit(0)
sys.exit('unexpected kubectl: ' + ' '.join(a))
''')
        for p in (ssh_stub, crictl_stub, ctr_stub, curl_stub, kubectl_stub):
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

        def reset_nodes():
            for host in ('one', 'two'):
                hroot = root / host
                mdir = hroot / 'etc/kubernetes/manifests'
                mdir.mkdir(parents=True, exist_ok=True)
                for p in list((hroot / 'etc/kubernetes/patches').glob('*')) if (hroot / 'etc/kubernetes/patches').exists() else []:
                    p.unlink()
                if (hroot / 'etc/kubernetes/patches').exists():
                    (hroot / 'etc/kubernetes/patches').rmdir()
                for f in hroot.glob('.cid-hist-*.json'):
                    f.unlink()
                for comp in ALL_STATIC_PODS:
                    (mdir / f'{comp}.yaml').write_text(sample_manifest(comp))
            ev = root / 'events.log'
            if ev.exists():
                ev.unlink()

        reset_nodes()
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
            assert 'dry-run complete' in r.stdout and 'allowPrivilegeEscalation: false' in r.stdout, r.stdout
            assert 'etcd' not in r.stdout, f'dry-run must not diff etcd:\n{r.stdout}'
            for h in ('one', 'two'):
                assert not (root / h / 'backups').exists()
                assert not (root / h / 'etc/kubernetes/patches').exists()
                assert (root / h / 'etc/kubernetes/manifests/etcd.yaml').read_text() == sample_manifest('etcd')
            if scenario == 'dry':
                return

        if scenario in ('all', 'etcd_untouched'):
            r_bad = run(nodes=('one=u@one',), extra=('--component', 'etcd'), fail=True)
            assert 'unsupported --component: etcd' in r_bad.stderr, r_bad.stderr
            run(nodes=('one=u@one',), extra=('--apply',))
            assert (root / 'one/etc/kubernetes/manifests/etcd.yaml').read_text() == sample_manifest('etcd')
            assert not (root / 'one/etc/kubernetes/patches/etcd+strategic.yaml').exists()
            bdirs = list((root / 'one/backups').glob('run.*'))
            assert bdirs and not (bdirs[0] / 'etcd.yaml').exists()
            reset_nodes()
            if scenario == 'etcd_untouched':
                return

        if scenario in ('all', 'component_select'):
            # Invalid component rejected
            run(nodes=('one=u@one',), extra=('--component', 'coredns'), fail=True)
            # Single component (with duplicate flag) updates ONLY kube-scheduler
            run(nodes=('one=u@one',), extra=('--component', 'kube-scheduler', '--component', 'kube-scheduler', '--apply'))
            assert 'allowPrivilegeEscalation: false' in (root / 'one/etc/kubernetes/manifests/kube-scheduler.yaml').read_text()
            assert (root / 'one/etc/kubernetes/manifests/kube-controller-manager.yaml').read_text() == sample_manifest('kube-controller-manager')
            assert (root / 'one/etc/kubernetes/manifests/kube-apiserver.yaml').read_text() == sample_manifest('kube-apiserver')
            assert (root / 'one/etc/kubernetes/manifests/etcd.yaml').read_text() == sample_manifest('etcd')
            assert sorted(p.name for p in (root / 'one/etc/kubernetes/patches').iterdir()) == ['kube-scheduler+strategic.yaml']
            # Repeatable --component updates controller-manager and apiserver, leaves etcd untouched
            run(nodes=('one=u@one',), extra=('--component', 'kube-controller-manager', '--component', 'kube-apiserver', '--apply'))
            for comp in COMPONENTS:
                assert 'allowPrivilegeEscalation: false' in (root / f'one/etc/kubernetes/manifests/{comp}.yaml').read_text()
            assert (root / 'one/etc/kubernetes/manifests/etcd.yaml').read_text() == sample_manifest('etcd')
            reset_nodes()
            if scenario == 'component_select':
                return

        if scenario in ('all', 'node_health'):
            # A. Mirror pod API fails, yet node-side crictl + local health endpoint pass -> apply SUCCEEDS
            r_ok = run(nodes=('one=u@one',), extra=('--component', 'kube-apiserver', '--apply'), ov={'API_MIRROR_FAIL': '1'})
            assert 'healthy one/kube-apiserver' in r_ok.stdout, r_ok.stdout
            assert 'mirror-pod report (non-blocking): pending or API unreachable' in r_ok.stderr, r_ok.stderr
            reset_nodes()

            # ctr fallback: crictl is absent, ctr reports a RUNNING hardened container.
            crictl_stub.rename(bindir / 'crictl.disabled')
            try:
                r_ctr = run(nodes=('one=u@one',), extra=('--component', 'kube-scheduler', '--apply'))
                assert 'healthy one/kube-scheduler' in r_ctr.stdout, r_ctr.stdout
            finally:
                (bindir / 'crictl.disabled').rename(crictl_stub)
            reset_nodes()

            # B. Mirror pod would claim Ready, yet crictl inspect shows unhardened securityContext -> MUST FAIL and rollback
            r_sc = run(nodes=('one=u@one',), extra=('--component', 'kube-scheduler', '--apply'), fail=True,
                       ov={'BAD_RUNTIME_SC': 'one/kube-scheduler'})
            assert 'health timeout: one/kube-scheduler' in r_sc.stderr, r_sc.stderr
            assert (root / 'one/etc/kubernetes/manifests/kube-scheduler.yaml').read_text() == sample_manifest('kube-scheduler')
            reset_nodes()

            # C. crictl inspect shows hardened Running container, yet local HTTPS healthz fails -> MUST FAIL and rollback
            r_hz = run(nodes=('one=u@one',), extra=('--component', 'kube-scheduler', '--apply'), fail=True,
                       ov={'HEALTHZ_FAIL': 'one/kube-scheduler'})
            assert 'health timeout: one/kube-scheduler' in r_hz.stderr, r_hz.stderr
            assert (root / 'one/etc/kubernetes/manifests/kube-scheduler.yaml').read_text() == sample_manifest('kube-scheduler')
            reset_nodes()
            if scenario == 'node_health':
                return

        if scenario in ('all', 'logs_before_restore'):
            # Scheduler succeeds; controller-manager fails healthz -> diagnostics captured BEFORE restore
            r_diag = run(nodes=('one=u@one',), extra=('--apply',), fail=True,
                         ov={'HEALTHZ_FAIL': 'one/kube-controller-manager'})
            m = re.search(
                r'saved diagnostics before restore: one/kube-controller-manager path=(/var/lib/narwhal/control-plane-security-context/(run\.\S+)/diagnostics/kube-controller-manager)',
                r_diag.stderr,
            )
            assert m, f'missing saved diagnostics path in stderr:\n{r_diag.stderr}'
            run_name = m.group(2)
            diag_dir = root / 'one/backups' / run_name / 'diagnostics/kube-controller-manager'
            assert (diag_dir / 'current.log').is_file() and 'comp=kube-controller-manager' in (diag_dir / 'current.log').read_text()
            assert (diag_dir / 'previous.log').is_file() and 'comp=kube-controller-manager' in (diag_dir / 'previous.log').read_text()
            assert (diag_dir / 'current-inspect.json').is_file() and json.loads((diag_dir / 'current-inspect.json').read_text())['status']['metadata']['name'] == 'kube-controller-manager'
            assert (diag_dir / 'previous-inspect.json').is_file() and json.loads((diag_dir / 'previous-inspect.json').read_text())['status']['metadata']['name'] == 'kube-controller-manager'

            # Ordering proof: crictl logs + crictl inspect for failing component happened BEFORE restore
            ev_lines = (root / 'events.log').read_text().splitlines()
            log_idx = next((i for i, l in enumerate(ev_lines) if l.startswith('crictl-logs:one:kube-controller-manager:')), -1)
            restore_idx = next((i for i, l in enumerate(ev_lines) if l == 'restore:one:kube-controller-manager'), -1)
            assert log_idx >= 0 and restore_idx >= 0 and log_idx < restore_idx, (
                f'expected crictl-logs before restore, got events={ev_lines}'
            )
            # Both changed manifests restored; etcd untouched
            for comp in ALL_STATIC_PODS:
                assert (root / f'one/etc/kubernetes/manifests/{comp}.yaml').read_text() == sample_manifest(comp)
            reset_nodes()
            if scenario == 'logs_before_restore':
                return

        if scenario in ('all', 'refuse'):
            bad_p = root / 'one/etc/kubernetes/manifests/kube-apiserver.yaml'
            bad_p.write_text(sample_manifest('kube-apiserver', '    securityContext:\n      allowPrivilegeEscalation: true\n'))
            run(nodes=('one=u@one',), extra=('--apply',), fail=True)
            bad_p.write_text(sample_manifest('kube-apiserver', '    securityContext:\n      capabilities:\n        add:\n        - DAC_OVERRIDE\n'))
            run(nodes=('one=u@one',), extra=('--apply',), fail=True)
            bad_p.write_text(sample_manifest('kube-apiserver'))
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
            r = run(nodes=('one=u@one',), extra=('--apply',), fail=True, ov={'CORRUPT_WRITE': 'kube-controller-manager'})
            assert 'changes fields outside securityContext hardening' in r.stderr
            for comp in ALL_STATIC_PODS:
                assert (root / f'one/etc/kubernetes/manifests/{comp}.yaml').read_text() == sample_manifest(comp)
            reset_nodes()
            if scenario == 'corrupt':
                return

        if scenario in ('all', 'rollback_partial'):
            r = run(extra=('--apply',), fail=True, ov={'HEALTHZ_FAIL': 'two/kube-controller-manager'})
            assert 'restored and healthy: two/kube-controller-manager' in r.stderr
            assert 'EARLIER NODE STILL APPLIED: one=u@one' in r.stderr
            for comp in ALL_STATIC_PODS:
                assert (root / f'two/etc/kubernetes/manifests/{comp}.yaml').read_text() == sample_manifest(comp)
            m = re.search(r'EARLIER NODE STILL APPLIED: one=u@one backup=(/var/lib/narwhal/control-plane-security-context/run\.\S+)', r.stderr)
            assert m, r.stderr
            # Manual --rollback must succeed even when API server is completely down!
            run(nodes=('one=u@one',), extra=('--rollback', m.group(1)), ov={'API_DOWN': '1'})
            for comp in ALL_STATIC_PODS:
                assert (root / f'one/etc/kubernetes/manifests/{comp}.yaml').read_text() == sample_manifest(comp)
            reset_nodes()
            if scenario == 'rollback_partial':
                return

        if scenario in ('all', 'rollback_health'):
            r = run(nodes=('one=u@one',), extra=('--apply',), fail=True,
                    ov={'CRICTL_NOT_RUNNING': 'one/kube-controller-manager', 'ROLLBACK_NOT_READY': 'one/kube-controller-manager'})
            assert 'ROLLBACK FAILED: one/kube-controller-manager' in r.stderr
            reset_nodes()
            if scenario == 'rollback_health':
                return

        if scenario in ('all', 'apply'):
            run(extra=('--apply',))
            for h in ('one', 'two'):
                # No backup/temp files leaked inside watched manifests dir; etcd untouched.
                assert sorted(p.name for p in (root / h / 'etc/kubernetes/manifests').iterdir()) == sorted(f'{c}.yaml' for c in ALL_STATIC_PODS)
                assert (root / h / 'etc/kubernetes/manifests/etcd.yaml').read_text() == sample_manifest('etcd')
                assert not (root / h / 'etc/kubernetes/patches/etcd+strategic.yaml').exists()
                for comp in COMPONENTS:
                    doc = yq_json((root / h / f'etc/kubernetes/manifests/{comp}.yaml').read_text())
                    assert doc['spec']['securityContext']['seccompProfile'] == {'type': 'RuntimeDefault'}
                    assert doc['spec']['containers'][0]['securityContext'] == {
                        'allowPrivilegeEscalation': False,
                        'capabilities': {'drop': ['ALL'], **({'add': ['NET_BIND_SERVICE']} if comp == 'kube-apiserver' else {})},
                    }
                    assert (root / h / f'etc/kubernetes/patches/{comp}+strategic.yaml').is_file()
            r_idem = run(extra=('--apply',))
            assert r_idem.stdout.count('backup=none') == 6


def check_ops_kd(files, scenario='all'):
    with tempfile.TemporaryDirectory(prefix='r426-kd-') as td:
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


def check_health_fail_closed(files):
    """crictl_health must fail closed on CRI-only inspect data (review finding, D-CP-HEALTH-EXPECTED)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        helper = td / 'helper.py'
        helper.write_text(files[HELPER_CP])
        expected = td / 'expected.yaml'
        expected.write_text("""apiVersion: v1
kind: Pod
metadata: {name: kube-apiserver, namespace: kube-system}
spec:
  securityContext: {seccompProfile: {type: RuntimeDefault}}
  containers:
  - name: kube-apiserver
    command: [kube-apiserver]
    securityContext:
      allowPrivilegeEscalation: false
      capabilities: {drop: [ALL], add: [NET_BIND_SERVICE]}
""")
        def run(cri_sc):
            inspect = td / 'inspect.json'
            inspect.write_text(json.dumps({'status': {'id': 'new', 'state': 'CONTAINER_RUNNING', 'metadata': {'name': 'kube-apiserver'}},
                                           'info': {'config': {'linux': {'security_context': cri_sc}}}}))
            return subprocess.run([sys.executable, str(helper), 'crictl-health', str(inspect), 'kube-apiserver', 'old', str(expected)],
                                  capture_output=True, text=True).returncode
        good = {'no_new_privs': True, 'seccomp': {'profile_type': 'RuntimeDefault'},
                'capabilities': {'drop_capabilities': ['ALL'], 'add_capabilities': ['NET_BIND_SERVICE']}}
        assert run(good) == 0, 'CRI-only healthy apiserver must pass'
        assert run({k: v for k, v in good.items() if k != 'seccomp'}) != 0, 'CRI-only inspect without seccomp must fail closed'
        assert run({**good, 'capabilities': {'drop_capabilities': ['ALL'], 'add_capabilities': []}}) != 0, 'missing NET_BIND_SERVICE must fail'
        assert run({**good, 'capabilities': {'drop_capabilities': ['ALL'], 'add_capabilities': ['NET_BIND_SERVICE', 'SYS_ADMIN']}}) != 0, 'extra capability must fail'
        assert run({**good, 'no_new_privs': False}) != 0, 'no_new_privs false must fail'


def main():
    files = {name: (ROOT / name).read_text() for name in FILES}
    check_static(files)
    check_health_fail_closed(files)
    check_ops_cp(files, 'all')
    check_ops_kd(files, 'all')

    if '--mutation-verify' in sys.argv:
        mutations = [
            ('drop capabilities.drop in apiserver patch', PATCH_FILES[2], '- ALL', '- NET_RAW', check_static),
            ('drop NET_BIND_SERVICE from apiserver patch', PATCH_FILES[2], '            - NET_BIND_SERVICE', '', check_static),
            ('crictl health trusts CRI-only data without seccomp', HELPER_CP, '            if not isinstance(runtime_spec, dict):\n                assert isinstance(cri_seccomp, dict)', '            if False:\n                assert isinstance(cri_seccomp, dict)', check_health_fail_closed),
            ('crictl health ignores CRI add_capabilities', HELPER_CP, "assert 'ALL' in dropped and added == want", "assert 'ALL' in dropped", check_health_fail_closed),
            ('drop NET_BIND_SERVICE from renderer', HELPER_CP, "ADD_CAPS = {'kube-apiserver': ['NET_BIND_SERVICE']}", 'ADD_CAPS = {}', check_static),
            ('add DAC_OVERRIDE to apiserver patch', PATCH_FILES[2], '            - NET_BIND_SERVICE', '            - NET_BIND_SERVICE\n            - DAC_OVERRIDE', check_static),
            ('drop seccompProfile in scheduler patch', PATCH_FILES[0], 'type: RuntimeDefault', 'type: Unconfined', check_static),
            ('re-add etcd+strategic.yaml in 02-init-cluster.sh', INIT, '  kube-apiserver+strategic.yaml \\', '  etcd+strategic.yaml \\\n  kube-apiserver+strategic.yaml \\', check_static),
            ('re-add etcd+strategic.yaml in 02-join-control-plane.sh', JOIN_CP, '  kube-apiserver+strategic.yaml \\', '  etcd+strategic.yaml \\\n  kube-apiserver+strategic.yaml \\', check_static),
            ('re-add etcd to ALL_COMPONENTS in ops-cp', OPS_CP, 'ALL_COMPONENTS=(kube-scheduler kube-controller-manager kube-apiserver)', 'ALL_COMPONENTS=(kube-scheduler kube-controller-manager etcd kube-apiserver)', check_static),
            ('drop patches.directory in 02-init-cluster.sh', INIT, 'directory: /etc/kubernetes/patches', 'directory: /tmp/disabled', check_static),
            ('drop protectKernelDefaults in 02-init-cluster.sh', INIT, 'protectKernelDefaults: true', 'protectKernelDefaults: false', check_static),
            ('drop --patches in 02-join-control-plane.sh', JOIN_CP, ' --patches=/etc/kubernetes/patches', '', check_static),
            ('drop sysctl guard in 02-join-worker.sh', JOIN_W, 'ensure_kubelet_kernel_defaults', 'true', check_static),
            ('drop sysctl guard in 01-prerequisites.sh', PREREQ, 'ensure_kubelet_kernel_defaults', 'true', check_static),
            ('drift kernel.panic in 95-kubelet-kernel-defaults.conf', SYSCTL_CONF, 'kernel.panic = 10', 'kernel.panic = 0', check_static),
            ('change etcd:etcd to root:root in harden-node-files.sh', HARDEN, 'chown -R etcd:etcd', 'chown -R root:root', check_static),
            ('drop etcd KSV003 exact acceptance', RULES, '"name": "etcd-narwhal-master-1",\n      "checks": [\n        "KSV001",\n        "KSV003",\n        "KSV004",', '"name": "etcd-narwhal-master-1",\n      "checks": [\n        "KSV001",\n        "KSV004",', check_static),
            ('accept KSV022 on etcd in portal rules', RULES, '"KSV023",\n        "KSV024"\n      ],\n      "class": "accept",\n      "reasonKey": "compliance.acceptedReason.etcdStaticPodTrialFailed"', '"KSV022",\n        "KSV023",\n        "KSV024"\n      ],\n      "class": "accept",\n      "reasonKey": "compliance.acceptedReason.etcdStaticPodTrialFailed"', check_static),
            ('accept KSV001 on kube-apiserver in portal rules', RULES, '"name": "kube-apiserver-narwhal-master-1",\n      "checks": [\n        "KSV009",', '"name": "kube-apiserver-narwhal-master-1",\n      "checks": [\n        "KSV001",\n        "KSV009",', check_static),
            ('widen apiserver file-capability KSV022 rule', RULES,
             '"checks": [\n        "KSV022"\n      ],\n      "class": "accept",\n      "reasonKey": "compliance.acceptedReason.apiserverNetBindFileCap"',
             '"checks": [\n        "KSV022",\n        "KSV106"\n      ],\n      "class": "accept",\n      "reasonKey": "compliance.acceptedReason.apiserverNetBindFileCap"', check_static),
            ('drop R426 registration', REGRESSION, 'check R426 ', 'echo R426 ', check_static),
            ('allow --component etcd in ops-cp', OPS_CP, 'kube-scheduler|kube-controller-manager|kube-apiserver) ;;', 'kube-scheduler|kube-controller-manager|kube-apiserver|etcd) ;;', lambda fs: check_ops_cp(fs, 'etcd_untouched')),
            ('ignore --component selection in ops-cp', OPS_CP, 'if ((${#SELECTED_COMPONENTS[@]} == 0)); then', 'if true; then', lambda fs: check_ops_cp(fs, 'component_select')),
            ('skip local curl health endpoint check in ops-cp', OPS_CP, 'curl -skf --connect-timeout 5 --max-time 10 "$url" >/dev/null 2>&1 || exit 1', ':', lambda fs: check_ops_cp(fs, 'node_health')),
            ('skip crictl securityContext verification in helper-cp', HELPER_CP, 'if not needs_sc_check:\n        return', 'return', lambda fs: check_ops_cp(fs, 'node_health')),
            ('remove ctr fallback from node-health', OPS_CP, '      ctr_bin=$(find_ctr) || exit 1\n      cid=$(ctr_running_cid "$comp") || exit 1', '      exit 1', lambda fs: check_ops_cp(fs, 'node_health')),
            ('make mirror-pod report blocking in ops-cp', OPS_CP, 'echo "mirror-pod report (non-blocking): pending or API unreachable for $NODE/$comp" >&2', 'return 1', lambda fs: check_ops_cp(fs, 'node_health')),
            ('skip saving diagnostics before restore in ops-cp', OPS_CP, 'if diag_path=$(remote save-diagnostics "$failing_comp" "$NODE_BACKUP_DIR"); then', 'if false; then', lambda fs: check_ops_cp(fs, 'logs_before_restore')),
            ('save diagnostics AFTER restore instead of before in ops-cp', OPS_CP,
             '      local failing_comp="${NODE_CHANGED[${#NODE_CHANGED[@]}-1]}"\n'
             '      local diag_path=""\n'
             '      if diag_path=$(remote save-diagnostics "$failing_comp" "$NODE_BACKUP_DIR"); then\n'
             '        echo "saved diagnostics before restore: $NODE/$failing_comp path=$diag_path" >&2\n'
             '      else\n'
             '        echo "WARNING: failed to save diagnostics for $NODE/$failing_comp into $NODE_BACKUP_DIR" >&2\n'
             '      fi\n'
             '      echo "restoring $NODE from $NODE_BACKUP_DIR" >&2\n'
             '      local failed_restore=0 pre_restore_cid=""\n'
             '      for ((i=${#NODE_CHANGED[@]}-1; i>=0; i--)); do\n'
             '        comp="${NODE_CHANGED[i]}"\n'
             '        fetch "$NODE_BACKUP_DIR/$comp.yaml" > "$WORK/restore-$comp.yaml" || { failed_restore=1; continue; }\n'
             '        pre_restore_cid=$(remote current-cid "$comp" 2>/dev/null || true)\n'
             '        if remote restore "$comp" "$NODE_BACKUP_DIR" && wait_health "$comp" "$pre_restore_cid" "$WORK/restore-$comp.yaml"; then\n'
             '          echo "restored and healthy: $NODE/$comp" >&2\n'
             '          report_mirror_pod "$comp" "" "$WORK/restore-$comp.yaml"\n'
             '        else\n'
             '          echo "ROLLBACK FAILED: $NODE/$comp" >&2\n'
             '          failed_restore=1\n'
             '        fi\n'
             '      done',
             '      echo "restoring $NODE from $NODE_BACKUP_DIR" >&2\n'
             '      local failed_restore=0 pre_restore_cid=""\n'
             '      for ((i=${#NODE_CHANGED[@]}-1; i>=0; i--)); do\n'
             '        comp="${NODE_CHANGED[i]}"\n'
             '        fetch "$NODE_BACKUP_DIR/$comp.yaml" > "$WORK/restore-$comp.yaml" || { failed_restore=1; continue; }\n'
             '        pre_restore_cid=$(remote current-cid "$comp" 2>/dev/null || true)\n'
             '        if remote restore "$comp" "$NODE_BACKUP_DIR" && wait_health "$comp" "$pre_restore_cid" "$WORK/restore-$comp.yaml"; then\n'
             '          echo "restored and healthy: $NODE/$comp" >&2\n'
             '          report_mirror_pod "$comp" "" "$WORK/restore-$comp.yaml"\n'
             '        else\n'
             '          echo "ROLLBACK FAILED: $NODE/$comp" >&2\n'
             '          failed_restore=1\n'
             '        fi\n'
             '      done\n'
             '      local failing_comp="${NODE_CHANGED[${#NODE_CHANGED[@]}-1]}"\n'
             '      local diag_path=""\n'
             '      if diag_path=$(remote save-diagnostics "$failing_comp" "$NODE_BACKUP_DIR"); then\n'
             '        echo "saved diagnostics before restore: $NODE/$failing_comp path=$diag_path" >&2\n'
             '      fi',
             lambda fs: check_ops_cp(fs, 'logs_before_restore')),
            ('skip st_dev check in ops-cp', OPS_CP, '[[ -n "$tmp_dev" && "$tmp_dev" == "$man_dev" ]]', ':', lambda fs: check_ops_cp(fs, 'stdev')),
            ('stage temp inside watched manifests dir in ops-cp', OPS_CP, 'tmpdir=/etc/kubernetes/.narwhal-tmp', 'tmpdir=/etc/kubernetes/manifests/.narwhal-tmp', lambda fs: check_ops_cp(fs, 'apply')),
            ('dry-run mutates in ops-cp', OPS_CP, 'if ((APPLY == 0)); then\n  echo "dry-run complete', 'if ((APPLY == 99)); then\n  echo "dry-run complete', lambda fs: check_ops_cp(fs, 'dry')),
            ('skip post-write verify in ops-cp', OPS_CP, 'python3 "$HELPER" verify "$old" "$WORK/$NODE/$comp.written" "$comp"', ':', lambda fs: check_ops_cp(fs, 'corrupt')),
            ('skip rollback health check in ops-cp', OPS_CP, 'if remote restore "$comp" "$NODE_BACKUP_DIR" && wait_health "$comp" "$pre_restore_cid" "$WORK/restore-$comp.yaml"; then', 'if remote restore "$comp" "$NODE_BACKUP_DIR"; then', lambda fs: check_ops_cp(fs, 'rollback_health')),
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
        mutated = dict(files)
        mutated_rules = json.loads(mutated[RULES])
        mutated_rules['configAudit'] = [r for r in mutated_rules['configAudit']
                                        if r.get('id') != 'configAudit:kube-system:Pod:kube-apiserver-narwhal-master-1:net-bind-filecap']
        mutated[RULES] = json.dumps(mutated_rules)
        try:
            check_static(mutated)
        except (AssertionError, KeyError, IndexError, ValueError):
            pass
        else:
            raise AssertionError('mutation survived: remove apiserver file-capability KSV022 rule')
        print(f'R426 PASS: {len(mutations) + 1} mutations rejected')

    print('R426 PASS: control-plane static Pod kubeadm patches (D-CP-ETCD-DEFER, --component, node-side crictl+healthz, pre-restore logs), etcd:etcd preserved, CIS 4.2.6 protectKernelDefaults + sysctl ordering')


if __name__ == '__main__':
    main()
