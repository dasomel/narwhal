#!/usr/bin/env python3
"""R309 (D-VM-DISK-GUARD): guards against unbounded sparse-vmdk growth.

Pins (1) the NodeDiskWriteRateHigh alert (threshold, window, control-plane exclusion, runbook
anchor; promtool behavior cases when available), (2) scripts/ops/check-vm-disk.sh thresholds and
--strict exit codes through its fixture seam, (3) the up.sh warn-only hook and (4) the runbook's
safe order (node-local data check, drain, destroy). --mutation-verify re-runs the checks against
copies of the files with one defect each and requires every defect to be rejected.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = ['gitops/resources/prometheus-alerts.yaml', 'scripts/ops/check-vm-disk.sh',
         'scripts/up.sh', 'docs/vagrant/operations.md']
ALERT = 'NodeDiskWriteRateHigh'
HEADING = '## 12. VM 디스크 증가와 회수 (D-VM-DISK-GUARD)'


def slug(heading):
    text = heading.lstrip('#').strip().lower()
    return re.sub(r'\s', '-', re.sub(r'[^\w\s-]', '', text))


def static(root):
    rules = yaml.safe_load((root / FILES[0]).read_text())
    found = [r for g in rules['spec']['groups'] for r in g['rules'] if r.get('alert') == ALERT]
    assert len(found) == 1, 'alert missing'
    rule = found[0]
    expr = ' '.join(rule['expr'].split())
    assert '[6h]' in expr and '* 86400 > 20e9' in expr, expr
    assert 'unless on (instance) node_uname_info{nodename=~".*master.*"}' in expr, expr
    assert rule['labels']['severity'] == 'warning' and rule['for'] == '30m'
    docs = (root / FILES[3]).read_text()
    assert docs.count(HEADING + '\n') == 1, 'runbook heading'
    url = rule['annotations']['runbook_url']
    assert url.endswith('/docs/vagrant/operations.md#' + slug(HEADING)), url
    sec = docs.split(HEADING + '\n', 1)[1].split('\n## ', 1)[0]
    pos = [sec.find(k) for k in ('노드 로컬 데이터 확인', 'kubectl drain', 'vagrant destroy -f')]
    assert all(p >= 0 for p in pos) and pos == sorted(pos), pos
    assert 'check-vm-disk.sh' in sec and 'seaweedfs-volume=true' in sec
    up = (root / FILES[2]).read_text()
    assert 'bash scripts/ops/check-vm-disk.sh || true' in up, 'up.sh hook'
    return rule


def run_script(root, fixture, free, *args, **env):
    with tempfile.NamedTemporaryFile('w', suffix='.fx') as fx:
        fx.write(fixture)
        fx.flush()
        e = dict(os.environ, VM_DISK_FIXTURE=fx.name, VM_DISK_FREE_GB=str(free), **env)
        p = subprocess.run(['/bin/bash', str(root / FILES[1]), *args], env=e,
                           capture_output=True, text=True)
    return p.returncode, p.stdout


def script(root):
    # healthy: fresh clone has a negative gap, 29G gap and 119G are under both limits
    rc, out = run_script(root, 'worker-1 9 30\nworker-2 73 44\nworker-3 119 100\n', 428, '--strict')
    assert rc == 0 and 'WARN' not in out, out
    # gap 30 and abs 120 are the first values that warn (boundary)
    rc, out = run_script(root, 'worker-2 74 44\n', 428)
    assert rc == 0 and out.count('[WARN] worker-2') == 1, out
    rc, out = run_script(root, 'worker-2 120 110\n', 428)
    assert out.count('[WARN] worker-2') == 1, out
    # --strict turns a warning into exit 1; default stays 0 so up.sh is never blocked
    assert run_script(root, 'worker-2 74 44\n', 428, '--strict')[0] == 1
    assert run_script(root, 'worker-2 74 44\n', 428)[0] == 0
    # host free space: 39G warns, 40G does not
    assert '[WARN] host free space 39G' in run_script(root, 'worker-1 9 30\n', 39)[1]
    assert 'WARN' not in run_script(root, 'worker-1 9 30\n', 40)[1]
    # a VM that is not created must not fail the check
    rc, out = run_script(root, 'worker-1  \n', 428, '--strict')
    assert rc == 0 and '[SKIP] worker-1' in out, out
    # thresholds are overridable
    assert '[WARN]' in run_script(root, 'worker-2 20 5\n', 428, VM_DISK_WARN_GAP_GB='10')[1]


def behavior(rule):
    if not shutil.which('promtool'):
        print('SKIP: promtool unavailable; R309 behavior cases not run')
        return
    mb = lambda n: f'0+{n}000000x500'  # counter, 1m scrape: n MB/min
    uname = lambda inst, node: {'series': f'node_uname_info{{instance="{inst}",nodename="{node}"}}',
                                'values': '1+0x500'}
    disk = lambda inst, n: {'series': f'node_disk_written_bytes_total{{instance="{inst}",device="nvme0n1"}}',
                            'values': mb(n)}

    def case(name, series, firing):
        exp = [{'exp_labels': {'instance': firing, 'severity': 'warning'}}] if firing else []
        return {'name': name, 'interval': '1m', 'input_series': series,
                'alert_rule_test': [{'eval_time': '7h', 'alertname': ALERT, 'exp_alerts': exp}]}

    cases = [
        # 20MB/min = 28.8 GB/day on a worker fires; 5MB/min = 7.2 GB/day does not
        case('busy worker', [disk('w1', 20), uname('w1', 'narwhal-worker-1')], 'w1'),
        case('quiet worker', [disk('w1', 5), uname('w1', 'narwhal-worker-1')], None),
        # control planes write 27-54 GB/day (etcd, NFS) without growing; excluded
        case('busy master', [disk('m1', 40), uname('m1', 'narwhal-master-1')], None),
        # a worker with no uname series is still caught (unless must not drop it)
        case('busy worker without uname', [disk('w2', 20)], 'w2'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'rules.yaml').write_text(yaml.safe_dump({'groups': [{'name': 'g', 'rules': [
            {k: v for k, v in rule.items() if k != 'annotations'}]}]}))
        (t / 'tests.yaml').write_text(yaml.safe_dump({
            'rule_files': ['rules.yaml'], 'evaluation_interval': '1m', 'tests': cases}))
        subprocess.run(['promtool', 'check', 'rules', str(t / 'rules.yaml')], check=True)
        subprocess.run(['promtool', 'test', 'rules', str(t / 'tests.yaml')], check=True)
    print(f'PASS: {len(cases)} promtool behavior cases')


def check(root):
    rule = static(root)
    script(root)
    behavior(rule)


MUTATIONS = [
    ('alert threshold 20e9 -> 200e9', FILES[0], '* 86400 > 20e9', '* 86400 > 200e9'),
    ('alert window 6h -> 5m', FILES[0], 'device=~"nvme[0-9]+n[0-9]+|sd[a-z]+|vd[a-z]+"}[6h]',
     'device=~"nvme[0-9]+n[0-9]+|sd[a-z]+|vd[a-z]+"}[5m]'),
    ('alert drops control-plane exclusion', FILES[0],
     'unless on (instance) node_uname_info{nodename=~".*master.*"}', ''),
    ('alert runbook anchor broken', FILES[0], '#12-vm-디스크-증가와-회수-d-vm-disk-guard', '#vm-disk'),
    ('gap check becomes exclusive', FILES[1], '[ "${gap}" -ge "${WARN_GAP_GB}" ]', '[ "${gap}" -gt "${WARN_GAP_GB}" ]'),
    ('abs check dropped', FILES[1], ' || [ "${h}" -ge "${WARN_ABS_GB}" ]; then', '; then'),
    ('free space check dropped', FILES[1], '[ "${FREE_GB}" -lt "${MIN_FREE_GB}" ]', 'false'),
    ('strict no longer exits 1', FILES[1], 'exit 1\nexit 0', 'exit 0\nexit 0'),
    ('up.sh hook removed', FILES[2], 'bash scripts/ops/check-vm-disk.sh || true', ': # removed'),
    ('runbook data-check step removed', FILES[3], '노드 로컬 데이터 확인', 'DATA'),
]


def mutation_verify():
    for name, rel, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for f in FILES:
                (t / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / f, t / f)
            text = (t / rel).read_text()
            assert text.count(old) >= 1, f'mutation target missing: {name}'
            (t / rel).write_text(text.replace(old, new, 1))
            try:
                check(t)
            except (AssertionError, subprocess.CalledProcessError):
                print(f'caught: {name}')
                continue
            raise SystemExit(f'MUTATION SURVIVED: {name}')
    print(f'PASS: {len(MUTATIONS)} mutations rejected')


if __name__ == '__main__':
    check(ROOT)
    print('PASS: R309 alert, script thresholds, up.sh hook, runbook order')
    if '--mutation-verify' in sys.argv:
        mutation_verify()
