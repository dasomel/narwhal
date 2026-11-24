#!/usr/bin/env python3
"""Local render/semantic and live health checks for the bounded GC flag edit (yq v4)."""
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime

FLAG = '--terminated-pod-gc-threshold=12500'


def parse(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', '.', str(path)]))


def command(doc):
    containers = [c for c in doc['spec']['containers'] if c['name'] == 'kube-controller-manager']
    assert len(containers) == 1, 'expected one controller-manager container'
    return containers[0]['command']


def verify(old, new):
    expected = copy.deepcopy(parse(old))
    args = command(expected)
    existing = [a for a in args if a.startswith('--terminated-pod-gc-threshold')]
    assert existing in ([], [FLAG]), 'conflicting GC flag: require owner review'
    if not existing:
        args.append(FLAG)
    assert parse(new) == expected, 'candidate changes more than the GC argument'


def render(old, new):
    text = Path(old).read_text()
    args = command(parse(old))
    if FLAG in args:
        Path(new).write_text(text)
    else:
        # D7: preserve comments/quotes with one insertion; cost: kubeadm block-list
        # shape required. Escape: reject other shapes for a reviewed renderer.
        lines = text.splitlines(keepends=True)
        starts = [i for i, line in enumerate(lines) if re.fullmatch(r'\s*- kube-controller-manager\s*\n?', line)]
        assert len(starts) == 1, 'ambiguous command block'
        last = starts[0]
        for i in range(last + 1, len(lines)):
            if re.match(r'^\s*- --', lines[i]):
                last = i
            else:
                break
        assert last > starts[0], 'no existing -- argument'
        indent = re.match(r'^(\s*)', lines[last])[1]
        lines.insert(last + 1, f'{indent}- {FLAG}\n')
        Path(new).write_text(''.join(lines))
    verify(old, new)


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def health(pods_path, lease_path, node, old_uid, expected, baseline):
    pods = json.loads(Path(pods_path).read_text())['items']
    lease = json.loads(Path(lease_path).read_text())['spec']
    target = next(p for p in pods if p['metadata']['name'] == f'kube-controller-manager-{node}')
    def ready(p):
        return not p['metadata'].get('deletionTimestamp') and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p.get('status', {}).get('conditions', []))
    assert ready(target)
    assert not old_uid or target['metadata']['uid'] != old_uid
    assert command(target) == command(parse(expected))
    holder = lease['holderIdentity']
    assert any(ready(p) and holder.startswith(p['spec']['nodeName'] + '_') for p in pods), 'Lease holder is not a current Ready controller pod'
    started = next(c['state']['running']['startedAt'] for c in target['status']['containerStatuses'] if c['name'] == 'kube-controller-manager')
    assert timestamp(lease['renewTime']) > timestamp(started), 'Lease predates new process'
    assert timestamp(lease['renewTime']) > timestamp(baseline), 'Lease did not advance'


if __name__ == '__main__':
    try:
        {'render': render, 'verify': verify, 'health': health}[sys.argv[1]](*sys.argv[2:])
    except (AssertionError, KeyError, StopIteration, ValueError) as exc:
        print(f'control-plane verification failed: {exc}', file=sys.stderr)
        sys.exit(1)
