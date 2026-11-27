#!/usr/bin/env python3
"""R422: EventRateLimit bootstrap, join, ops rollout, and mutation checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
INIT = 'scripts/cluster/02-init-cluster.sh'
JOIN = 'scripts/cluster/02-join-control-plane.sh'
KAKAO = 'scripts/cloud/distribute-join-kakao.sh'
OPS = 'scripts/ops/apply-apiserver-admission.sh'
HELPER = 'scripts/ops/lib/apiserver-admission.py'
ADMISSION_CFG = 'scripts/ops/lib/admission-config.yaml'
REGRESSION = 'scripts/test/regression-check-kakao.sh'
FILES = (INIT, JOIN, KAKAO, OPS, HELPER, ADMISSION_CFG, REGRESSION)

CONFIG_PATH = '/etc/kubernetes/admission/admission-config.yaml'
PLUGINS_FLAG = '--enable-admission-plugins=NodeRestriction,EventRateLimit'
CONFIG_FLAG = f'--admission-control-config-file={CONFIG_PATH}'

MANIFEST_NODERESTRICTION = '''# keep this comment
apiVersion: v1
kind: Pod
metadata:
  name: kube-apiserver
  namespace: kube-system
spec:
  containers:
  - command:
    - kube-apiserver
    - --authorization-mode=Node,RBAC
    - --enable-admission-plugins=NodeRestriction
    - --profiling=false
    image: registry.k8s.io/kube-apiserver:v1.35.7
    name: kube-apiserver
    volumeMounts:
    - mountPath: /etc/kubernetes/pki
      name: k8s-certs
      readOnly: true
  volumes:
  - hostPath:
      path: /etc/kubernetes/pki
      type: DirectoryOrCreate
    name: k8s-certs
'''

MANIFEST_NO_PLUGINS = '''# keep this comment too
apiVersion: v1
kind: Pod
metadata:
  name: kube-apiserver
  namespace: kube-system
spec:
  containers:
  - command:
    - kube-apiserver
    - --authorization-mode=Node,RBAC
    - --profiling=false
    image: registry.k8s.io/kube-apiserver:v1.35.7
    name: kube-apiserver
    volumeMounts:
    - mountPath: /etc/kubernetes/pki
      name: k8s-certs
      readOnly: true
  volumes:
  - hostPath:
      path: /etc/kubernetes/pki
      type: DirectoryOrCreate
    name: k8s-certs
'''

CLUSTER_CFG = '''apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: v1.35.7
apiServer:
  extraArgs:
    - name: profiling
      value: "false"
    - name: enable-admission-plugins
      value: "NodeRestriction"
  extraVolumes:
    - name: enc
      hostPath: /etc/kubernetes/enc
      mountPath: /etc/kubernetes/enc
      readOnly: true
      pathType: DirectoryOrCreate
'''


def yq_json(text):
    return json.loads(subprocess.check_output(['yq', '-o=json', '.'], input=text, text=True))


def check_static(files):
    init_text = files[INIT]
    admission_text = files[ADMISSION_CFG]

    heredoc = init_text.split("cat <<'ERLEOF' | sudo tee /etc/kubernetes/admission/admission-config.yaml >/dev/null\n", 1)[1].split('\nERLEOF\n', 1)[0] + '\n'
    assert heredoc == admission_text, '02-init-cluster.sh ERLEOF heredoc drifted from scripts/ops/lib/admission-config.yaml'

    adm = yq_json(admission_text)
    assert adm == {
        'apiVersion': 'apiserver.config.k8s.io/v1',
        'kind': 'AdmissionConfiguration',
        'plugins': [{
            'name': 'EventRateLimit',
            'configuration': {
                'apiVersion': 'eventratelimit.admission.k8s.io/v1alpha1',
                'kind': 'Configuration',
                'limits': [
                    {'type': 'Server', 'qps': 500, 'burst': 1000},
                    {'type': 'Namespace', 'qps': 50, 'burst': 100, 'cacheSize': 2000},
                ],
            },
        }],
    }, f'unexpected admission-config.yaml: {adm}'

    assert 'sudo install -d -o root -g root -m 700 /etc/kubernetes/admission' in init_text
    assert 'sudo chown root:root /etc/kubernetes/admission/admission-config.yaml' in init_text
    assert 'sudo chmod 600 /etc/kubernetes/admission/admission-config.yaml' in init_text
    assert 'sudo install -o vagrant -g vagrant -m 600 /etc/kubernetes/admission/admission-config.yaml /home/vagrant/admission-config.yaml' in init_text
    assert init_text.index('ERLEOF') < init_text.index('sudo kubeadm init ')
    assert init_text.index('/home/vagrant/admission-config.yaml') < init_text.index('sudo kubeadm init ')

    kubeadm_raw = init_text.split('cat <<EOF > /tmp/kubeadm-config.yaml\n', 1)[1].split('\nEOF\n', 1)[0]
    kubeadm_doc = yq_json(
        kubeadm_raw.replace('${CERT_SANS}', '\n    - localhost')
        .replace('${K8S_PATCH_VERSION}', '1.35.7')
        .replace('${VIP_ADDRESS}', '192.168.56.100')
        .replace('${POD_NETWORK_CIDR}', '10.244.0.0/16')
        .replace('${SERVICE_CIDR}', '10.96.0.0/12')
        .split('\n---\n', 1)[0]
    )
    extra_args = kubeadm_doc['apiServer']['extraArgs']
    assert {'name': 'enable-admission-plugins', 'value': 'NodeRestriction,EventRateLimit'} in extra_args
    assert {'name': 'admission-control-config-file', 'value': CONFIG_PATH} in extra_args
    assert {
        'name': 'admission',
        'hostPath': '/etc/kubernetes/admission',
        'mountPath': '/etc/kubernetes/admission',
        'readOnly': True,
        'pathType': 'DirectoryOrCreate',
    } in kubeadm_doc['apiServer']['extraVolumes']

    join_text = files[JOIN]
    assert 'sudo install -d -o root -g root -m 700 /etc/kubernetes/admission' in join_text
    assert '[[ -f /tmp/admission-config.yaml ]] && sudo install -o root -g root -m 600 /tmp/admission-config.yaml /etc/kubernetes/admission/admission-config.yaml' in join_text
    assert 'vagrant@${MASTER1_IP}:/home/vagrant/admission-config.yaml' in join_text
    assert '[[ -f /etc/kubernetes/admission/admission-config.yaml ]]' in join_text
    assert join_text.index('/etc/kubernetes/admission/admission-config.yaml') < join_text.index('$(cat /tmp/join-control-plane.sh)')

    kakao_text = files[KAKAO]
    assert 'for f in join-control-plane.sh join-command.sh encryption-config.yaml admission-config.yaml; do' in kakao_text
    assert 'place "${ip}" admission-config.yaml /tmp/admission-config.yaml' in kakao_text

    reg_text = files[REGRESSION]
    assert 'check R421 ' in reg_text and 'python3 scripts/test/lib/check-falco-least-privileged.py --mutation-verify' in reg_text
    assert 'check R422 ' in reg_text and 'python3 scripts/test/lib/check-apiserver-eventratelimit.py --mutation-verify' in reg_text


def _setup_stubs(root, files):
    bindir = root / 'bin'
    bindir.mkdir()

    ssh = bindir / 'ssh'
    ssh.write_text(r'''#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path

a = sys.argv
assert 'BatchMode=yes' in a and 'StrictHostKeyChecking=yes' in a
assert 'ServerAliveInterval=10' in a and 'ServerAliveCountMax=3' in a
assert 'sudo' in a
host = next(v for v in a if '@' in v).split('@')[1]
nroot = Path(os.environ['NODE_ROOT']) / host

def rewrite(s):
    return (
        s.replace('/etc/kubernetes', str(nroot / 'etc/kubernetes'))
        .replace('/var/lib/narwhal/apiserver-admission', str(nroot / 'backups'))
    )

if 'cat' in a:
    p = rewrite(a[-1])
    sys.stdout.write(Path(p).read_text())
    sys.exit(0)

script_text = rewrite(sys.stdin.read())
args = [rewrite(v) for v in a[a.index('--') + 1:]]
res = subprocess.run(['bash', '-s', '--'] + args, input=script_text, text=True, capture_output=True)
sys.stdout.write(res.stdout.replace(str(nroot / 'backups'), '/var/lib/narwhal/apiserver-admission'))
sys.stderr.write(res.stderr)
if res.returncode == 0 and args[0] == 'apply' and os.environ.get('CORRUPT_WRITE'):
    p = nroot / 'etc/kubernetes/manifests/kube-apiserver.yaml'
    p.write_text(p.read_text().replace('kube-apiserver:v1.35.7', 'kube-apiserver:v1.35.7-corrupt'))
sys.exit(res.returncode)
''')

    kubectl = bindir / 'kubectl'
    kubectl.write_text(r'''#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys
from pathlib import Path

root = Path(os.environ['NODE_ROOT'])
a = sys.argv
assert a[1] == '--kubeconfig'
cm_path = root / 'kubeadm.json'

if 'node' in a:
    print(json.dumps({'metadata': {'labels': {'node-role.kubernetes.io/control-plane': ''}}}))
    sys.exit(0)

if 'kubeadm-config' in a:
    if 'patch' in a:
        assert '--type=json' in a
        patch = json.loads(Path(a[a.index('--patch-file') + 1]).read_text())
        doc = json.loads(cm_path.read_text())
        assert patch[0] == {'op': 'test', 'path': '/metadata/resourceVersion', 'value': doc['metadata']['resourceVersion']}
        assert patch[1]['op'] == 'replace' and patch[1]['path'] == '/data/ClusterConfiguration'
        doc['data']['ClusterConfiguration'] = patch[1]['value']
        doc['metadata']['resourceVersion'] = str(int(doc['metadata']['resourceVersion']) + 1)
        cm_path.write_text(json.dumps(doc))
        with (root / 'events').open('a') as f:
            f.write('patch\n')
    print(cm_path.read_text())
    sys.exit(0)

if '/readyz' in a:
    if os.environ.get('APISERVER_DOWN'):
        sys.exit(1)
    print('ok')
    sys.exit(0)

if 'pod' in a:
    host = a[a.index('pod') + 1].removeprefix('kube-apiserver-')
    manifest = root / host / 'etc/kubernetes/manifests/kube-apiserver.yaml'
    raw = manifest.read_text()
    doc = json.loads(subprocess.check_output(['yq', '-o=json', '.', str(manifest)], text=True))
    uid = hashlib.sha256(raw.encode()).hexdigest()
    if any('jsonpath' in v for v in a):
        print(uid)
        sys.exit(0)
    cmd = list(doc['spec']['containers'][0]['command'])
    if os.environ.get('STALE_CMD') == host:
        cmd = [x for x in cmd if not x.startswith('--admission-control-config-file=')]
    has_target = '--enable-admission-plugins=NodeRestriction,EventRateLimit' in cmd
    ready = 'True'
    if has_target and os.environ.get('NOT_READY') == host:
        ready = 'False'
    if (not has_target) and os.environ.get('ROLLBACK_NOT_READY') == host:
        ready = 'False'
    doc['metadata'] = {'name': f'kube-apiserver-{host}', 'uid': uid}
    doc['spec']['containers'][0]['command'] = cmd
    doc['status'] = {'conditions': [{'type': 'Ready', 'status': ready}]}
    with (root / 'events').open('a') as f:
        f.write(f'pod {host} ready={ready}\n')
    print(json.dumps(doc))
    sys.exit(0)

sys.exit('unexpected kubectl: ' + ' '.join(a))
''')

    stat_stub = bindir / 'stat'
    stat_stub.write_text(r'''#!/usr/bin/env python3
import os, sys
if len(sys.argv) == 4 and sys.argv[1] == '-c':
    fmt, path = sys.argv[2], sys.argv[3]
    if fmt == '%d':
        if os.environ.get('BAD_DEVICE') and '.narwhal-tmp' in path:
            print(999999)
        else:
            print(os.stat(path).st_dev)
        sys.exit(0)
    if fmt == '%u:%g:%a':
        st = os.stat(path)
        print(f'0:0:{st.st_mode & 0o777:o}')
        sys.exit(0)
sys.exit(2)
''')

    install_stub = bindir / 'install'
    install_stub.write_text(r'''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
mode = None
is_dir = False
owner = None
group = None
paths = []
i = 0
while i < len(args):
    if args[i] == '-d':
        is_dir = True; i += 1
    elif args[i] == '-m':
        mode = int(args[i + 1], 8); i += 2
    elif args[i] == '-o':
        owner = args[i + 1]; i += 2
    elif args[i] == '-g':
        group = args[i + 1]; i += 2
    else:
        paths.append(args[i]); i += 1
if owner or group:
    with (Path(os.environ['NODE_ROOT']) / 'root_ops').open('a') as f:
        f.write(f'install {owner}:{group} {oct(mode) if mode is not None else ""} {paths[-1]}\n')
if is_dir:
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)
        if mode is not None:
            os.chmod(p, mode)
else:
    src, dst = Path(paths[0]), Path(paths[1])
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    if mode is not None:
        os.chmod(dst, mode)
''')

    chown_stub = bindir / 'chown'
    chown_stub.write_text(r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
with (Path(os.environ['NODE_ROOT']) / 'root_ops').open('a') as f:
    f.write(f'chown {" ".join(sys.argv[1:])}\n')
''')

    for p in (ssh, kubectl, stat_stub, install_stub, chown_stub):
        p.chmod(0o755)

    ops_copy = root / 'ops/apply-apiserver-admission.sh'
    ops_copy.parent.mkdir(parents=True, exist_ok=True)
    ops_copy.write_text(files[OPS])
    (ops_copy.parent / 'lib').mkdir(exist_ok=True)
    (ops_copy.parent / 'lib/apiserver-admission.py').write_text(files[HELPER])
    (ops_copy.parent / 'lib/admission-config.yaml').write_text(files[ADMISSION_CFG])
    return ops_copy


def check_ops(files, scenario='all'):
    with tempfile.TemporaryDirectory(prefix='r422-') as td:
        root = Path(td)
        ops_copy = _setup_stubs(root, files)
        manifests = {}
        for host, content in (('one', MANIFEST_NODERESTRICTION), ('two', MANIFEST_NO_PLUGINS)):
            m = root / host / 'etc/kubernetes/manifests/kube-apiserver.yaml'
            m.parent.mkdir(parents=True)
            m.write_text(content)
            manifests[host] = m
        cm_path = root / 'kubeadm.json'
        cm_path.write_text(json.dumps({
            'metadata': {'resourceVersion': '1'},
            'data': {'ClusterConfiguration': CLUSTER_CFG},
        }))
        kubeconfig = root / 'admin.conf'
        kubeconfig.touch()
        events = root / 'events'
        events.write_text('')

        env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}", NODE_ROOT=td)
        base = ['bash', str(ops_copy), '--kubeconfig', str(kubeconfig), '--timeout', '1']

        def run(nodes=('one=user@one', 'two=user@two'), extra=(), fail=False, overrides=None):
            cmd = list(base)
            for n in nodes:
                cmd += ['--node', n]
            cmd += list(extra)
            res = subprocess.run(cmd, env=env | (overrides or {}), text=True, capture_output=True, timeout=30)
            assert (res.returncode != 0) == fail, f'rc={res.returncode}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}'
            return res

        # 1. Dry-run by default: prints diff, zero remote/cluster writes.
        if scenario in ('all', 'dry'):
            r = run()
            assert f'+    - {PLUGINS_FLAG}' in r.stdout, r.stdout
            assert f'+    - {CONFIG_FLAG}' in r.stdout, r.stdout
            assert '+    - mountPath: /etc/kubernetes/admission' in r.stdout, r.stdout
            assert '+      name: admission' in r.stdout, r.stdout
            assert '=== dry-run kube-system/kubeadm-config ===' in r.stdout, r.stdout
            assert manifests['one'].read_text() == MANIFEST_NODERESTRICTION
            assert manifests['two'].read_text() == MANIFEST_NO_PLUGINS
            assert json.loads(cm_path.read_text())['metadata']['resourceVersion'] == '1'
            assert 'patch' not in events.read_text()
            for h in ('one', 'two'):
                assert not (root / h / 'backups').exists()
                assert not (root / h / 'etc/kubernetes/.narwhal-tmp').exists()
                assert not (root / h / 'etc/kubernetes/admission').exists()
            if scenario == 'dry':
                return

        # 2. Refusal cases: unexpected enable-admission-plugins, wrong config file, bad ClusterConfiguration.
        if scenario in ('all', 'refuse'):
            manifests['one'].write_text(MANIFEST_NODERESTRICTION.replace('NodeRestriction', 'NodeRestriction,AlwaysAdmit'))
            run(nodes=('one=user@one',), extra=('--apply',), fail=True)
            assert 'AlwaysAdmit' in manifests['one'].read_text()
            manifests['one'].write_text(
                MANIFEST_NODERESTRICTION.replace('--profiling=false', '--admission-control-config-file=/etc/kubernetes/other.yaml\n    - --profiling=false')
            )
            run(nodes=('one=user@one',), extra=('--apply',), fail=True)
            manifests['one'].write_text(MANIFEST_NODERESTRICTION)
            bad_cm = json.dumps({'metadata': {'resourceVersion': '1'}, 'data': {'ClusterConfiguration': CLUSTER_CFG.replace('NodeRestriction', 'AlwaysAdmit')}})
            cm_path.write_text(bad_cm)
            run(nodes=('one=user@one',), extra=('--apply',), fail=True)
            cm_path.write_text(json.dumps({'metadata': {'resourceVersion': '1'}, 'data': {'ClusterConfiguration': CLUSTER_CFG}}))
            if scenario == 'refuse':
                return

        # 3. Same-filesystem st_dev rejection & corrupt write rejection.
        if scenario in ('all', 'stdev'):
            run(nodes=('one=user@one',), extra=('--apply',), fail=True, overrides={'BAD_DEVICE': '1'})
            assert manifests['one'].read_text() == MANIFEST_NODERESTRICTION
            if scenario == 'stdev':
                return

        if scenario in ('all', 'corrupt'):
            r = run(nodes=('one=user@one',), extra=('--apply',), fail=True, overrides={'CORRUPT_WRITE': '1'})
            assert 'candidate changes fields outside' in r.stderr, r.stderr
            assert manifests['one'].read_text() == MANIFEST_NODERESTRICTION
            if scenario == 'corrupt':
                return

        # 4. Partial multi-node failure: node one succeeds, node two times out and rolls back + verifies Ready.
        if scenario in ('all', 'rollback_partial'):
            events.write_text('')
            r = run(extra=('--apply',), fail=True, overrides={'NOT_READY': 'two'})
            assert manifests['two'].read_text() == MANIFEST_NO_PLUGINS
            assert 'restored and healthy: two' in r.stderr, r.stderr
            assert 'EARLIER NODE STILL APPLIED: one=user@one' in r.stderr, r.stderr
            assert '--rollback /var/lib/narwhal/apiserver-admission/manifest.' in r.stderr, r.stderr
            assert 'patch' not in events.read_text(), 'kubeadm-config must not be patched on partial failure'
            # Roll back node one using the exact backup path printed to stderr.
            import re
            m = re.search(r'EARLIER NODE STILL APPLIED: one=user@one backup=(/var/lib/narwhal/apiserver-admission/manifest\.\S+)', r.stderr)
            assert m, r.stderr
            run(nodes=('one=user@one',), extra=('--rollback', m.group(1)))
            assert manifests['one'].read_text() == MANIFEST_NODERESTRICTION
            if scenario == 'rollback_partial':
                return

        # 5. Rollback health failure must report ROLLBACK FAILED (not silent success).
        if scenario in ('all', 'rollback_health'):
            r = run(nodes=('one=user@one',), extra=('--apply',), fail=True, overrides={'NOT_READY': 'one', 'ROLLBACK_NOT_READY': 'one'})
            assert 'ROLLBACK FAILED: one' in r.stderr, r.stderr
            assert manifests['one'].read_text() == MANIFEST_NODERESTRICTION
            if scenario == 'rollback_health':
                return

        # 6. Full apply across both nodes + kubeadm-config patch + idempotency.
        if scenario in ('all', 'apply'):
            events.write_text('')
            (root / 'root_ops').unlink(missing_ok=True)
            run(extra=('--apply',))
            for h, orig in (('one', MANIFEST_NODERESTRICTION), ('two', MANIFEST_NO_PLUGINS)):
                # Only the static pod YAML lives in manifests/ (no backup/temp files).
                assert [p.name for p in manifests[h].parent.iterdir()] == ['kube-apiserver.yaml']
                assert manifests[h].stat().st_mode & 0o777 == 0o600
                rendered = manifests[h].read_text()
                assert '# keep this comment' in rendered
                assert PLUGINS_FLAG in rendered and CONFIG_FLAG in rendered
                assert 'mountPath: /etc/kubernetes/admission' in rendered
                adm_installed = root / h / 'etc/kubernetes/admission/admission-config.yaml'
                assert adm_installed.read_text() == files[ADMISSION_CFG]
                assert adm_installed.stat().st_mode & 0o777 == 0o600
                assert adm_installed.parent.stat().st_mode & 0o777 == 0o700
                backups = list((root / h / 'backups').glob('manifest.*'))
                assert backups and backups[-1].stat().st_mode & 0o777 == 0o600
            root_ops = (root / 'root_ops').read_text()
            assert 'install root:root 0o600' in root_ops or 'chown root:root' in root_ops
            assert 'patch\n' in events.read_text()
            cm_after = json.loads(cm_path.read_text())
            assert cm_after['metadata']['resourceVersion'] == '2'
            cfg_after = yq_json(cm_after['data']['ClusterConfiguration'])
            assert {'name': 'enable-admission-plugins', 'value': 'NodeRestriction,EventRateLimit'} in cfg_after['apiServer']['extraArgs']
            assert {'name': 'admission-control-config-file', 'value': CONFIG_PATH} in cfg_after['apiServer']['extraArgs']
            assert any(v.get('name') == 'admission' and v.get('readOnly') is True for v in cfg_after['apiServer']['extraVolumes'])

            # Idempotent re-run: no new backups, no kubeadm-config patch.
            backup_counts = {h: len(list((root / h / 'backups').glob('manifest.*'))) for h in ('one', 'two')}
            events.write_text('')
            r_idem = run(extra=('--apply',))
            assert 'backup=none' in r_idem.stdout
            assert 'kubeadm-config: already up to date' in r_idem.stdout
            assert 'patch' not in events.read_text()
            for h in ('one', 'two'):
                assert len(list((root / h / 'backups').glob('manifest.*'))) == backup_counts[h]

            # Semantic negative checks on helper verify & verify-config.
            bad_m = root / 'bad_manifest.yaml'
            bad_m.write_text(manifests['one'].read_text().replace('--profiling=false', '--profiling=true'))
            orig_m = root / 'orig_manifest.yaml'
            orig_m.write_text(MANIFEST_NODERESTRICTION)
            neg = subprocess.run(['python3', str(ops_copy.parent / 'lib/apiserver-admission.py'), 'verify', str(orig_m), str(bad_m)], capture_output=True)
            assert neg.returncode != 0, 'unrelated manifest edit accepted'

            orig_c = root / 'orig_cfg.yaml'
            bad_c = root / 'bad_cfg.yaml'
            orig_c.write_text(CLUSTER_CFG)
            bad_c.write_text(cm_after['data']['ClusterConfiguration'].replace('false', 'true'))
            neg_c = subprocess.run(['python3', str(ops_copy.parent / 'lib/apiserver-admission.py'), 'verify-config', str(orig_c), str(bad_c)], capture_output=True)
            assert neg_c.returncode != 0, 'unrelated ClusterConfiguration edit accepted'


def main():
    files = {name: (ROOT / name).read_text() for name in FILES}
    check_static(files)
    check_ops(files, 'all')

    if '--mutation-verify' in sys.argv:
        mutations = [
            ('drop EventRateLimit in 02-init-cluster.sh', INIT, 'value: "NodeRestriction,EventRateLimit"', 'value: "NodeRestriction"', check_static),
            ('drop admission extraVolume in 02-init-cluster.sh', INIT, '    - name: admission\n', '    - name: admission-dropped\n', check_static),
            ('drift Server budget in 02-init-cluster.sh', INIT, 'qps: 500, burst: 1000', 'qps: 100, burst: 200', check_static),
            ('drop join fetch in 02-join-control-plane.sh', JOIN, 'admission-config.yaml', 'other-config.yaml', check_static),
            ('drop kakao join staging in distribute-join-kakao.sh', KAKAO, ' admission-config.yaml', '', check_static),
            ('break R421/R422 in regression-check-kakao.sh', REGRESSION, 'check R422 ', 'echo R422 ', check_static),
            ('skip st_dev check in ops script', OPS, '[[ -n "$tmp_dev" && "$tmp_dev" == "$man_dev" ]]', ':', lambda fs: check_ops(fs, 'stdev')),
            ('stage temp inside watched manifests dir', OPS, 'tmpdir=/etc/kubernetes/.narwhal-tmp', 'tmpdir=/etc/kubernetes/manifests/.narwhal-tmp', lambda fs: check_ops(fs, 'apply')),
            ('dry-run mutates cluster', OPS, 'if ((APPLY == 0)); then\n    echo "=== dry-run $NODE ($HOST) ==="', 'if ((APPLY == 99)); then\n    echo "=== dry-run $NODE ($HOST) ==="', lambda fs: check_ops(fs, 'dry')),
            ('skip post-write semantic verify', OPS, 'python3 "$HELPER" verify "$WORK/old" "$WORK/written"', ':', lambda fs: check_ops(fs, 'corrupt')),
            ('skip Ready wait after timeout rollback', OPS, 'if remote restore "$BACKUP" && wait_health; then', 'if remote restore "$BACKUP"; then', lambda fs: check_ops(fs, 'rollback_health')),
            ('drop earlier node rollback tracking', OPS, 'if [[ -n "$BACKUP" ]]; then APPLIED+=("$mapping"); BACKUPS+=("$BACKUP"); fi', ':', lambda fs: check_ops(fs, 'rollback_partial')),
            ('skip kubeadm-config resourceVersion test', OPS, '{"op": "test", "path": "/metadata/resourceVersion", "value": cm["metadata"]["resourceVersion"]},', '', lambda fs: check_ops(fs, 'apply')),
            ('skip kubeadm-config patch', OPS, 'kube -n kube-system patch cm kubeadm-config --type=json --patch-file "$WORK/cluster-patch.json" -o json > "$WORK/kubeadm-after.json"', 'cp "$WORK/kubeadm.json" "$WORK/kubeadm-after.json"', lambda fs: check_ops(fs, 'apply')),
            ('accept unexpected plugins in helper', HELPER, "raise AssertionError(f'unexpected enable-admission-plugins value: {val}')", 'pass', lambda fs: check_ops(fs, 'refuse')),
            ('skip semantic equality in helper verify', HELPER, "assert after == expected, 'candidate changes fields outside the EventRateLimit edit'", 'assert True', lambda fs: check_ops(fs, 'apply')),
        ]
        for name, fname, old, new, runner in mutations:
            assert old in files[fname], f'mutation anchor missing for {name}'
            mutated = {**files, fname: files[fname].replace(old, new, 1)}
            try:
                runner(mutated)
            except (AssertionError, KeyError, IndexError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            raise AssertionError(f'mutation survived: {name}')
        print(f'R422 PASS: {len(mutations)} mutations rejected')

    print('R422 PASS: EventRateLimit init/join parity, dry-run, bounded edits, refusals, st_dev/0600 backups, rollback health, kubeadm-config patch')


if __name__ == '__main__':
    main()
