#!/usr/bin/env python3
"""R403: execute SSH/kubectl stubs, real yq and remote shell in a disposable node."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/ops/apply-control-plane-flags.sh'


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bindir = root / 'bin'
        bindir.mkdir()
        manifest = root / 'manifests/kube-controller-manager.yaml'
        manifest.parent.mkdir()
        original = 'spec:\n  containers:\n    - name: kube-controller-manager\n      command:\n        - kube-controller-manager\n        - --profiling=false\n'
        manifest.write_text(original)
        config = root / 'config'
        config.touch()
        ssh = bindir / 'ssh'
        ssh.write_text('''#!/usr/bin/env python3
import os,sys,subprocess
from pathlib import Path
root=Path(os.environ['NODE_ROOT'])
s=sys.stdin.read().replace('/etc/kubernetes/manifests',str(root/'manifests')).replace('/var/lib/narwhal/control-plane-flags',str(root/'backups'))
a=sys.argv; start=a.index('--')
sys.exit(subprocess.run(['bash','-s','--']+a[start+1:],input=s,text=True).returncode)
''')
        kubectl = bindir / 'kubectl'
        kubectl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['NODE_ROOT']); args=sys.argv
assert args[1]=='--kubeconfig'
if 'node' in args:
 print(json.dumps({'metadata':{'labels':{'node-role.kubernetes.io/control-plane':''}}}));sys.exit()
if any('jsonpath' in a for a in args):print('old');sys.exit()
s=(root/'manifests/kube-controller-manager.yaml').read_text()
flag='--terminated-pod-gc-threshold=12500'
print(json.dumps({'metadata':{'uid':'new'},'spec':{'containers':[{'name':'kube-controller-manager','command':[flag] if flag in s else []}]},'status':{'conditions':[{'type':'Ready','status':'False' if os.environ.get('NOT_READY') else 'True'}]}}))
''')
        for p in (ssh, kubectl):
            p.chmod(0o755)
        env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'], NODE_ROOT=td)
        args = ['bash', str(SCRIPT), '--kubeconfig', str(config), '--node', 'narwhal-master-1=vagrant@192.168.56.10', '--timeout', '1']
        def run(extra=(), fail=False, overrides=None):
            result = subprocess.run(args + list(extra), env=env | (overrides or {}), text=True, capture_output=True)
            assert (result.returncode != 0) == fail, result.stdout + result.stderr
            return result
        result = run(['--dry-run'])
        assert '+        - --terminated-pod-gc-threshold=12500' in result.stdout, result.stdout
        assert manifest.read_text() == original
        run()
        first = manifest.read_text()
        backups = list((root / 'backups').glob('manifest.*'))
        assert len(backups) == 1 and backups[0].read_text() == original
        assert backups[0].stat().st_mode & 0o777 == 0o600
        assert manifest.stat().st_mode & 0o777 == 0o600
        assert not list(manifest.parent.glob('manifest.*'))
        run()
        assert manifest.read_text() == first
        assert len(list((root / 'backups').glob('manifest.*'))) == 1
        manifest.write_text(original)
        result = run(fail=True, overrides={'NOT_READY': '1'})
        assert 'restoring' in result.stderr and manifest.read_text() == original
        print('R403 PASS: dry-run diff; explicit kubeconfig; idempotent re-run; rollback on not-ready; external 0600 backup; manifest 0600')


if __name__ == '__main__':
    main()
