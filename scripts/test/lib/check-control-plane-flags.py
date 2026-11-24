#!/usr/bin/env python3
"""R403: run the real local renderer/remote shell/health gate against disposable nodes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/ops/apply-control-plane-flags.sh'
HELPER = ROOT / 'scripts/ops/lib/control-plane-flags.py'
ORIGINAL = '''# keep this comment
spec:
  containers:
    - name: kube-controller-manager
      command:
        - kube-controller-manager
        - --profiling=false
        - --oidc-prefix='oidc:'
      image: registry.k8s.io/kube-controller-manager:v1.35.7
'''
FLAG = '--terminated-pod-gc-threshold=12500'


def suite(script, helper):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bindir = root / 'bin'
        bindir.mkdir()
        manifests = []
        for host in ('one', 'two'):
            manifest = root / host / 'etc/kubernetes/manifests/kube-controller-manager.yaml'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(ORIGINAL)
            manifests.append(manifest)
        config = root / 'config'
        config.touch()
        ssh = bindir / 'ssh'
        ssh.write_text('''#!/usr/bin/env python3
import os,sys,subprocess
from pathlib import Path
a=sys.argv
assert 'BatchMode=yes' in a and 'StrictHostKeyChecking=yes' in a
assert 'ServerAliveInterval=10' in a and 'ServerAliveCountMax=3' in a
host=next(v for v in a if v.startswith('user@')).split('@')[1]
root=Path(os.environ['NODE_ROOT'])/host
if 'cat' in a:
 p=a[-1].replace('/etc/kubernetes',str(root/'etc/kubernetes')).replace('/var/lib/narwhal/control-plane-flags',str(root/'backups'))
 sys.stdout.write(Path(p).read_text());sys.exit()
s=sys.stdin.read().replace('/etc/kubernetes',str(root/'etc/kubernetes')).replace('/var/lib/narwhal/control-plane-flags',str(root/'backups'))
args=a[a.index('--')+1:]
args=[v.replace('/var/lib/narwhal/control-plane-flags',str(root/'backups')) for v in args]
result=subprocess.run(['bash','-s','--']+args,input=s,text=True,capture_output=True)
sys.stdout.write(result.stdout.replace(str(root/'backups'),'/var/lib/narwhal/control-plane-flags'))
sys.stderr.write(result.stderr)
if result.returncode == 0 and args[0] == 'apply' and os.environ.get('CORRUPT_WRITE'):
 p=root/'etc/kubernetes/manifests/kube-controller-manager.yaml'
 p.write_text(p.read_text().replace('profiling=false','profiling=true'))
sys.exit(result.returncode)
''')
        kubectl = bindir / 'kubectl'
        kubectl.write_text('''#!/usr/bin/env python3
import hashlib,json,os,sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
root=Path(os.environ['NODE_ROOT']);a=sys.argv
assert a[1]=='--kubeconfig'
flag='--terminated-pod-gc-threshold=12500'
def pod(host):
 s=(root/host/'etc/kubernetes/manifests/kube-controller-manager.yaml').read_text()
 args=['kube-controller-manager','--profiling=false',"--oidc-prefix='oidc:'"]
 if flag in s:args.append(flag)
 return {'metadata':{'name':'kube-controller-manager-'+host,'uid':hashlib.sha256(s.encode()).hexdigest()},'spec':{'nodeName':host,'containers':[{'name':'kube-controller-manager','command':args}]},'status':{'conditions':[{'type':'Ready','status':'False' if flag in s and os.environ.get('NOT_READY')==host else 'True'}], 'containerStatuses':[{'name':'kube-controller-manager','state':{'running':{'startedAt':'2027-01-01T00:00:00Z' if os.environ.get('FUTURE_START') else '2026-01-01T00:00:00Z'}}}]}}
if 'node' in a:
 print(json.dumps({'metadata':{'labels':{'node-role.kubernetes.io/control-plane':''}}}));sys.exit()
if 'lease' in a:
 tick=root/'tick'; n=int(tick.read_text())+1 if tick.exists() else 1;tick.write_text(str(n))
 renew=(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=n)).isoformat().replace('+00:00','Z')
 if os.environ.get('STALE_LEASE'):renew='2026-01-01T00:00:00Z'
 if os.environ.get('FROZEN_LEASE'):renew='2026-01-01T00:00:01Z'
 obj={'spec':{'holderIdentity':'ghost_uuid' if os.environ.get('WRONG_HOLDER') else 'one_uuid','renewTime':renew}}
 if any('jsonpath' in v for v in a):print(renew)
 else:print(json.dumps(obj))
 sys.exit()
if 'pods' in a:print(json.dumps({'items':[pod('one'),pod('two')]}));sys.exit()
host=a[a.index('pod')+1].removeprefix('kube-controller-manager-')
print(pod(host)['metadata']['uid'])
''')
        for p in (ssh, kubectl):
            p.chmod(0o755)
        # macOS host runs GNU stat as gstat; actual remote nodes use GNU stat.
        gstat = shutil.which('gstat')
        if gstat:
            (bindir / 'stat').symlink_to(gstat)
        real_stat = shutil.which('gstat') or shutil.which('stat')
        stat_stub = bindir/'stat'
        if stat_stub.is_symlink():
            stat_stub.unlink()
        stat_stub.write_text('#!/usr/bin/env python3\nimport os,subprocess,sys\nif os.environ.get("BAD_DEVICE") and ".narwhal-tmp" in sys.argv[-1]: print(999999)\nelse: sys.exit(subprocess.run(['+repr(real_stat)+']+sys.argv[1:]).returncode)\n')
        stat_stub.chmod(0o755)
        script_copy = root / 'ops/apply-control-plane-flags.sh'
        script_copy.parent.mkdir()
        script_copy.write_text(script)
        (script_copy.parent / 'lib').mkdir()
        (script_copy.parent / 'lib/control-plane-flags.py').write_text(helper)
        env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'], NODE_ROOT=td)
        args = ['bash', str(script_copy), '--kubeconfig', str(config), '--node', 'one=user@one', '--timeout', '1']
        def run(extra=(), fail=False, overrides=None):
            result = subprocess.run(args + list(extra), env=env | (overrides or {}), text=True, capture_output=True, timeout=35)
            assert (result.returncode != 0) == fail, result.stdout + result.stderr
            return result
        result = run(['--dry-run'])
        assert '+        - '+FLAG in result.stdout
        assert manifests[0].read_text() == ORIGINAL
        assert not (root/'one/backups').exists() and not (root/'one/etc/kubernetes/.narwhal-tmp').exists(), 'dry-run wrote remotely'
        run(overrides={'BAD_DEVICE':'1'}, fail=True)
        assert manifests[0].read_text() == ORIGINAL
        run()
        assert [p.name for p in manifests[0].parent.iterdir()] == ['kube-controller-manager.yaml'], 'temporary path inside watched directory'
        expected = ORIGINAL.replace("        - --oidc-prefix='oidc:'\n", "        - --oidc-prefix='oidc:'\n        - "+FLAG+'\n')
        assert manifests[0].read_text() == expected, 'edit must preserve original text'
        backups = list((root / 'one/backups').glob('manifest.*'))
        assert len(backups) == 1 and backups[0].read_text() == ORIGINAL
        assert backups[0].stat().st_mode & 0o777 == 0o600
        assert manifests[0].stat().st_mode & 0o777 == 0o600
        run()
        assert len(list((root / 'one/backups').glob('manifest.*'))) == 1
        backup = '/var/lib/narwhal/control-plane-flags/manifest.test_-123'
        (root/'one/backups/manifest.test_-123').write_text(ORIGINAL)
        run(['--rollback', backup])
        assert manifests[0].read_text() == ORIGINAL
        run(overrides={'NOT_READY':'one'}, fail=True)
        assert manifests[0].read_text() == ORIGINAL
        for mode in ('STALE_LEASE', 'FROZEN_LEASE', 'WRONG_HOLDER', 'FUTURE_START', 'CORRUPT_WRITE'):
            run(overrides={mode:'1'}, fail=True)
            assert manifests[0].read_text() == ORIGINAL
        # A stale Lease must fail explicit rollback too (no early success).
        run(['--rollback', backup], overrides={'FROZEN_LEASE':'1'}, fail=True)
        result = run(['--node','two=user@two'], overrides={'NOT_READY':'two'}, fail=True)
        assert manifests[0].read_text() == expected and manifests[1].read_text() == ORIGINAL
        assert 'EARLIER NODE STILL APPLIED: one=user@one' in result.stderr
        assert '--rollback /var/lib/narwhal/control-plane-flags/manifest.' in result.stderr
        assert 'restored and healthy: two' in result.stderr
        # The semantic comparator must reject unrelated edits.
        old = root/'old'; new = root/'new'
        old.write_text(ORIGINAL);new.write_text(expected.replace('profiling=false','profiling=true'))
        r=subprocess.run(['python3',str(script_copy.parent/'lib/control-plane-flags.py'),'verify',str(old),str(new)],capture_output=True)
        assert r.returncode != 0, 'unrelated YAML edit accepted'


def main():
    script, helper = SCRIPT.read_text(), HELPER.read_text()
    suite(script, helper)
    mutations = [
        ('skip same filesystem check', 'script', '[[ $(stat -c %d "$tmpdir") == "$(stat -c %d "$(dirname "$manifest")")" ]]', ':'),
        ('temp inside watched directory', 'script', 'tmpdir=/etc/kubernetes/.narwhal-tmp', 'tmpdir=/etc/kubernetes/manifests/.narwhal-tmp'),
        ('omit Lease gate', 'helper', "assert timestamp(lease['renewTime']) > timestamp(baseline)", 'assert True'),
        ('omit process-start gate', 'helper', "assert timestamp(lease['renewTime']) > timestamp(started)", 'assert True'),
        ('omit current holder gate', 'helper', "assert any(ready(p) and holder.startswith(p['spec']['nodeName'] + '_') for p in pods)", 'assert True'),
        ('lose earlier backups', 'script', 'APPLIED+=("$mapping"); BACKUPS+=("$BACKUP")', ':'),
        ('skip rollback health', 'script', 'remote restore "$ROLLBACK"\n    OLD_UID=""\n    wait_health', 'remote restore "$ROLLBACK"\n    OLD_UID=""'),
        ('dry-run remote write', 'script', 'if ((DRY_RUN)); then diff', 'if ((DRY_RUN)); then remote apply "$(base64 < "$WORK/old" | tr -d "\\n")" "$(base64 < "$WORK/expected" | tr -d "\\n")"; diff'),
        ('skip semantic check', 'helper', "assert parse(new) == expected", 'assert True'),
    ]
    # st_dev and temp placement are shell contracts, with a direct negative mutation.
    contracts = ['[[ $(stat -c %d "$tmpdir") == "$(stat -c %d "$(dirname "$manifest")")" ]]',
                 'tmpdir=/etc/kubernetes/.narwhal-tmp', 'chmod 600 "$candidate"',
                 'python3 "$HELPER" verify "$WORK/old" "$WORK/written"']
    assert all(c in script for c in contracts)
    if '--mutation-verify' in sys.argv:
        for name, key, old, new in mutations:
            source = helper if key == 'helper' else script
            assert old in source, name
            changed = source.replace(old, new, 1)
            try:
                suite(changed if key == 'script' else script, changed if key == 'helper' else helper)
            except (AssertionError, subprocess.TimeoutExpired):
                continue
            raise AssertionError('survived mutation: '+name)
        for contract in contracts:
            assert not all(c in script.replace(contract, '', 1) for c in contracts)
        print(f'R403 PASS: {len(mutations)+len(contracts)} mutations rejected')
    print('R403 PASS: read-only dry-run; minimal edit; semantic negative; idempotence; 0600 backups; Ready + advancing/current-holder Lease; rollback health; mixed-node failure recovery')


if __name__ == '__main__':
    main()
