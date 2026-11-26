#!/usr/bin/env python3
"""R267/R419: SeaweedFS master/filer keep state on NFS and run as non-root UID/GID 10100 with emptyDir logs, in the GitOps chart AND the 08-4 helm install; the align script's static
map and chart uid agree, defaults to dry run and never writes /etc/exports."""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import yaml

chart = open('gitops/charts/narwhal-apps/templates/seaweedfs.yaml').read()
sh = open('scripts/cluster/08-4-storage.sh').read()
align = open('scripts/cluster/nfs-root-squash-align.sh').read()

blocks = {
    'master': re.search(r'\n        master:\n(.*?)\n        volume:', chart, re.S),
    'filer': re.search(r'\n        filer:\n(.*?)\n        s3:', chart, re.S),
}
for comp, m in blocks.items():
    assert m, f'{comp} block not found'
    b = m.group(1)
    psc = re.search(r'podSecurityContext:\n((?:\s+.*\n)+?)\s+(?:logs|containerSecurityContext|s3):', b + '\n          logs:')
    assert psc, f'{comp} podSecurityContext'
    for want in ('enabled: true', 'runAsUser: 10100', 'runAsGroup: 10100', 'fsGroup: 10100', 'fsGroupChangePolicy: OnRootMismatch'):
        assert want in psc.group(1), f'{comp} chart {want}'
    assert re.search(r'logs:\n\s+type: emptyDir', b), f'{comp} logs must be emptyDir (hostPath is root-owned)'
    for key in ('enabled=true', 'runAsUser=10100', 'runAsGroup=10100', 'fsGroup=10100', 'fsGroupChangePolicy=OnRootMismatch'):
        assert f'--set {comp}.podSecurityContext.{key}' in sh, f'08-4 {comp} {key}'
    assert f'--set {comp}.logs.type=emptyDir' in sh, f'08-4 {comp} logs'

for pvc in ('storage/data-filer-seaweedfs-filer-0=10100:10100', 'storage/data-storage-seaweedfs-master-0=10100:10100'):
    assert f'"{pvc}"' in align, f'static map {pvc}'

volume = re.search(r'\n        volume:\n(.*?)\n        filer:', chart, re.S)
assert volume and all(x in volume.group(1) for x in ('runAsUser: 10100', 'runAsGroup: 10100', 'runAsNonRoot: true'))
for comp in ('master', 'volume', 'filer', 's3'):
    block = re.search(rf'\n        {comp}:\n(.*?)(?=\n        (?:master|volume|filer|s3):|\n  destination:)', chart, re.S)
    # D-sw-qos: CPU limits throttle S3 uploads, so none is set (KSV011 stays accepted).
    assert block and not re.search(r'limits:\n(?:\s+memory:.*\n)?\s+cpu:', block.group(1)), f'{comp} must not set a CPU limit'
assert 'install -d -m 0750 /ssd/object_store' in open('scripts/common/01-prerequisites.sh').read()
assert 'sudo chown 10100:10100 /ssd/object_store' in open('scripts/common/01-prerequisites.sh').read()
assert 'chown -R' not in open('scripts/common/01-prerequisites.sh').read()
assert 'APPLY=false' in align and '--apply) APPLY=true' in align, 'dry run must be the default'
code = [l for l in align.splitlines() if not l.lstrip().startswith('#')]
assert not any(re.search(r'(exportfs|/etc/exports.*(tee|sed -i|>>?)|(tee|sed -i|>>?).*/etc/exports)', l) and 'grep' not in l for l in code), 'must not edit exports'
assert 'no_root_squash' not in '\n'.join(code), 'script must not manipulate squash options'

# R419 renders both source-of-truth paths against the supplied offline 4.34.0 chart.
chart_dir = Path('.review/charts/seaweedfs')
if chart_dir.is_dir():
    app = yaml.safe_load(chart)
    bootstrap_values = sh.split("cat > /tmp/seaweedfs-values.yaml <<'EOF'\n", 1)[1].split('\nEOF', 1)[0]
    with tempfile.TemporaryDirectory(prefix='r419-render-') as tmp:
        tmp = Path(tmp)
        gitops_values = tmp / 'gitops.yaml'
        gitops_values.write_text(yaml.safe_dump(app['spec']['source']['helm']['valuesObject']))
        bootstrap_path = tmp / 'bootstrap.yaml'
        bootstrap_path.write_text(bootstrap_values)
        base = ['helm', 'template', 'seaweedfs', str(chart_dir), '-n', 'storage']
        contexts = [base + ['-f', str(gitops_values)], base + ['-f', str(bootstrap_path)]]
        for comp in ('master', 'filer', 's3'):
            for key, value in [('enabled', 'true'), ('runAsNonRoot', 'true'), ('runAsUser', '10100'), ('runAsGroup', '10100'), ('fsGroup', '10100'), ('fsGroupChangePolicy', 'OnRootMismatch')]:
                contexts[1] += ['--set', f'{comp}.podSecurityContext.{key}={value}']
        for key, value in [('enabled', 'true'), ('runAsNonRoot', 'true'), ('runAsUser', '10100'), ('runAsGroup', '10100')]:
            contexts[1] += ['--set', f'volume.podSecurityContext.{key}={value}']
        contexts[1] += ['--set', 's3.enabled=true']
        for context in contexts:
            result = subprocess.run(context, text=True, capture_output=True, check=True)
            workloads = {}
            for obj in yaml.safe_load_all(result.stdout):
                if not isinstance(obj, dict):
                    continue
                pod = obj.get('spec', {}).get('template', {}).get('spec', {})
                name = obj.get('metadata', {}).get('name')
                if pod and name in ('seaweedfs-master', 'seaweedfs-filer', 'seaweedfs-s3', 'seaweedfs-volume'):
                    workloads[name] = pod
            assert len(workloads) == 4, f'render expected 4 workloads, got {sorted(workloads)}'
            for name, pod in workloads.items():
                sec = pod['securityContext']
                assert sec['runAsNonRoot'] is True and sec['runAsUser'] == 10100 and sec['runAsGroup'] == 10100, (name, sec)
                if name != 'seaweedfs-volume':
                    assert sec['fsGroup'] == 10100 and sec['fsGroupChangePolicy'] == 'OnRootMismatch', (name, sec)
                assert 'cpu' not in pod['containers'][0].get('resources', {}).get('limits', {}), name

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mutation-verify', action='store_true')
    args = parser.parse_args()
    if args.mutation_verify:
        with tempfile.TemporaryDirectory(prefix='r419-seaweedfs-') as tmp:
            clone = Path(tmp)
            for path in ('gitops/charts/narwhal-apps/templates/seaweedfs.yaml', 'scripts/cluster/08-4-storage.sh', 'scripts/cluster/nfs-root-squash-align.sh', 'scripts/common/01-prerequisites.sh'):
                dest = clone / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            mutations = [
                ('gitops/charts/narwhal-apps/templates/seaweedfs.yaml', 'runAsUser: 10100', 'runAsUser: 1000'),
                ('gitops/charts/narwhal-apps/templates/seaweedfs.yaml', 'fsGroup: 10100', 'fsGroup: 1000'),
                ('scripts/cluster/08-4-storage.sh', 'runAsUser=10100', 'runAsUser=1000'),
                ('scripts/cluster/nfs-root-squash-align.sh', 'seaweedfs-master-0=10100:10100', 'seaweedfs-master-0=1000:1000'),
                ('scripts/common/01-prerequisites.sh', 'sudo chown 10100:10100 /ssd/object_store', 'sudo chown -R 10100:10100 /ssd/object_store'),
                ('gitops/charts/narwhal-apps/templates/seaweedfs.yaml', '            limits:\n              memory:', '            limits:\n              cpu: 500m\n              memory:'),
            ]
            pristine = {p: (clone / p).read_text() for p in {m[0] for m in mutations}}
            for path, old, new in mutations:
                text = pristine[path]
                assert old in text, ('mutation anchor missing', path, old)
                (clone / path).write_text(text.replace(old, new, 1))
                result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=clone, capture_output=True)
                (clone / path).write_text(text)
                if result.returncode == 0:
                    raise AssertionError(f'R419 accepted mutation {path}: {old!r} -> {new!r}')
        print(f'PASS: R419 SeaweedFS ownership invariants; {len(mutations)} mutations rejected')
sys.exit(0)
