#!/usr/bin/env python3
"""R342 GitOps half: execute staged-copy transforms and Helm renders, including mutations."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / 'scripts/gitops/apply-topology.sh'


def command(args, **kwargs):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30, **kwargs)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def cluster(text):
    return next(d for d in yaml.safe_load_all(text) if d and d.get('kind') == 'Cluster')


def publication(source, path, tree, target, topology):
    """Execute the publisher's actual copy section, without Git/network/cluster commands."""
    if path.endswith('14-gitops-bootstrap.sh'):
        snippet = source[source.index('cp -r /home/vagrant/configs/gitops/* .'):
                         source.index('# D-FALCO-045')]
        snippet = snippet.replace('/home/vagrant/configs/gitops', str(tree))
        snippet = snippet.replace('/home/vagrant/scripts/gitops/apply-topology.sh', str(HELPER))
    else:
        snippet = source[source.index('  #--- copy changed file(s)'):
                         source.index('#--- commit + push')]
        snippet = snippet[:snippet.rfind('\nfi')]
    command(['bash', '-ec', 'PATHS=(); log() { :; }; git() { :; };\n' + snippet],
            cwd=target, env=dict(os.environ, NARWHAL_TOPOLOGY=topology,
                                 GITOPS_DIR=str(tree), REPO_ROOT=str(ROOT)))


def check_publication(compare_head=False):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for path in ('scripts/cluster/14-gitops-bootstrap.sh', 'scripts/gitops/push-to-gitea.sh'):
            current = (ROOT / path).read_text()
            call = next(line for line in current.splitlines() if 'bash ' in line and 'apply-topology.sh' in line)
            previous = (command(['git', 'show', f'HEAD:{path}'], cwd=ROOT) if compare_head
                        else current.replace(call, ':'))
            trees = []
            for index, (source, topology) in enumerate(((previous, 'ha'), (current, 'ha'),
                                                       (current, ''), (current, 'single'))):
                target = root / f'{Path(path).stem}-{index}'
                target.mkdir()
                publication(source, path, ROOT / 'gitops', target, topology)
                trees.append({str(p.relative_to(target)): p.read_bytes()
                              for p in target.rglob('*') if p.is_file()})
            assert trees[0] == trees[1] == trees[2], f'{path}: HA publication bytes differ'
            assert cluster(trees[3]['resources/narwhal-db.yaml'].decode())['spec']['instances'] == 1
            # Removing the actual call must fail the same behavior assertion.
            target = root / f'{Path(path).stem}-mutant'
            target.mkdir()
            publication(current.replace(call, ':'), path, ROOT / 'gitops', target, 'single')
            assert cluster((target / 'resources/narwhal-db.yaml').read_text())['spec']['instances'] != 1
    baseline = 'pre-change HEAD' if compare_head else 'untransformed copy'
    print(f'ok: both publication paths match {baseline} bytes (ha/empty); single=1; 2 call-site mutations caught')


def check(helper_source):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        helper = root / 'apply-topology.sh'
        helper.write_text(helper_source)
        tree = root / 'gitops'
        shutil.copytree(ROOT / 'gitops', tree)
        before = {str(p.relative_to(tree)): p.read_bytes() for p in tree.rglob('*') if p.is_file()}
        apps = tree / 'charts/narwhal-apps'
        for topology in ('ha', '', 'single'):
            env = dict(os.environ, NARWHAL_TOPOLOGY=topology)
            command(['bash', str(helper), str(tree)], env=env)
            if topology != 'single':
                assert all((tree / p).read_bytes() == data for p, data in before.items()), 'HA tree changed'
            db = cluster((tree / 'resources/narwhal-db.yaml').read_text())
            assert db['spec']['instances'] == (1 if topology == 'single' else 2), 'GitOps CNPG instances'
            output = command(['helm', 'template', 'narwhal-apps', str(apps)])
            rendered = {d['metadata']['name']: d for d in yaml.safe_load_all(output) if d}
            values = lambda name: rendered[name]['spec']['source']['helm']['valuesObject']
            n = 1 if topology == 'single' else 2
            cert = values('cert-manager')
            assert [cert['replicaCount'], cert['webhook']['replicaCount'], cert['cainjector']['replicaCount']] == [n]*3
            assert cert['podDisruptionBudget']['minAvailable'] == 1  # drain remains limited
            assert values('istiod')['replicaCount'] == n
            kyverno = values('kyverno')
            assert [kyverno[k]['replicas'] for k in ('admissionController', 'backgroundController',
                    'cleanupController', 'reportsController')] == ([1]*4 if topology == 'single' else [3, 2, 2, 2])
            # Execute the exact bootstrap heredoc + profile transformation, before kubectl.
            source = (ROOT / 'scripts/cluster/07-cnpg.sh').read_text()
            snippet = source[source.index('CLUSTER_MANIFEST=$(mktemp)'):source.index('# Create unified PostgreSQL cluster')]
            snippet = snippet.replace('/home/vagrant/scripts/gitops/apply-topology.sh', str(helper))
            snippet += '\ncat "$CLUSTER_MANIFEST"; rm -f "$CLUSTER_MANIFEST"\n'
            bootstrap = command(['bash', '-ec', snippet], env=dict(env, POSTGRES_VERSION='18'))
            assert cluster(bootstrap)['spec']['instances'] == (1 if topology == 'single' else 2), 'bootstrap CNPG instances'
            assert db['spec']['affinity']['podAntiAffinityType'] == 'required'
        # Re-running a publish transformation is convergent.
        after = {str(p.relative_to(tree)): p.read_bytes() for p in tree.rglob('*') if p.is_file()}
        command(['bash', str(helper), str(tree)], env=dict(os.environ, NARWHAL_TOPOLOGY='single'))
        assert all((tree / p).read_bytes() == data for p, data in after.items())


def main():
    check_publication(compare_head='--compare-head' in sys.argv)
    # Execute the real kubelet override against a temporary multi-document config.
    init = (ROOT / 'scripts/cluster/02-init-cluster.sh').read_text()
    override = init[init.index('# D-SINGLE-NODE-PODS:'):init.index('# On cloud the Kakao LB')]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'kubeadm.yaml'
        before = 'kind: ClusterConfiguration\n---\nkind: KubeletConfiguration\ncgroupDriver: systemd\n'
        for topology in ('ha', 'single'):
            path.write_text(before)
            command(['bash', '-ec', override.replace('/tmp/kubeadm-config.yaml', str(path))],
                    env=dict(os.environ, NARWHAL_TOPOLOGY=topology))
            docs = list(yaml.safe_load_all(path.read_text()))
            assert docs[1].get('maxPods', 110) == (200 if topology == 'single' else 110)
            if topology == 'ha':
                assert path.read_text() == before
    source = HELPER.read_text()
    check(source)
    mutations = [
        ('cnpg-instance', '.spec.instances) = 1', '.spec.instances) = 2'),
        ('profile-not-persisted', '.admissionController.replicas = 1', '.admissionController.replicas = 3'),
        ('ha-transformed', '!= single', '= single'),
    ]
    for name, old, new in mutations:
        assert old in source, name
        try:
            check(source.replace(old, new))
        except AssertionError:
            continue
        raise AssertionError(f'mutation survived: {name}')
    # These are the actual publication call sites, not an alternate profile writer.
    for path in ('scripts/cluster/14-gitops-bootstrap.sh', 'scripts/gitops/push-to-gitea.sh'):
        assert 'apply-topology.sh" "$PWD"' in (ROOT / path).read_text(), path
    print('ok: staged HA bytes unchanged; bootstrap/published single CNPG=1; Helm replicas; 3 mutations caught')


if __name__ == '__main__':
    main()
