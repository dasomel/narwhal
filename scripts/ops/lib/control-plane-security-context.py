#!/usr/bin/env python3
"""Render, verify, and health-check control-plane static Pod securityContext edits (yq v4)."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

COMPONENTS = ('kube-scheduler', 'kube-controller-manager', 'etcd', 'kube-apiserver')
# D-CP-ETCD-DAC: /var/lib/etcd is intentionally etcd:etcd 0700 (CIS 1.1.12 / KCV0059) while
# kubeadm 1.35.7 runs etcd as UID 0, so etcd retains only DAC_OVERRIDE after dropping ALL.
# The other three static Pods drop ALL with no added capabilities.
# Cost: KSV022/KSV106 exact-accepted for etcd until kubeadm RootlessControlPlane is GA.
# Escape hatch: remove capabilities.add once etcd runs as the etcd UID.
COMPONENT_CAPS_ADD = {
    'etcd': ['DAC_OVERRIDE'],
}


def parse(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', '.', str(path)]))


def target_container(doc, component):
    assert component in COMPONENTS, f'unsupported component: {component}'
    containers = [c for c in doc['spec']['containers'] if c.get('name') == component]
    assert len(containers) == 1, f'expected exactly one {component} container'
    return containers[0]


def expected_pod_doc(before, component):
    assert before.get('apiVersion') == 'v1' and before.get('kind') == 'Pod', 'expected v1 Pod manifest'
    expected = copy.deepcopy(before)
    pod_sc = expected['spec'].setdefault('securityContext', {})
    assert isinstance(pod_sc, dict), 'spec.securityContext must be a mapping'
    seccomp = pod_sc.get('seccompProfile')
    if seccomp is None:
        pod_sc['seccompProfile'] = {'type': 'RuntimeDefault'}
    else:
        assert seccomp == {'type': 'RuntimeDefault'}, f'conflicting pod seccompProfile: {seccomp}'

    c = target_container(expected, component)
    sc = c.setdefault('securityContext', {})
    assert isinstance(sc, dict), f'{component} securityContext must be a mapping'
    assert sc.get('privileged') is not True, f'{component} must not be privileged'

    ape = sc.get('allowPrivilegeEscalation')
    if ape is None:
        sc['allowPrivilegeEscalation'] = False
    else:
        assert ape is False, f'conflicting allowPrivilegeEscalation on {component}: {ape}'

    caps = sc.setdefault('capabilities', {})
    assert isinstance(caps, dict), f'{component} capabilities must be a mapping'
    want_add = COMPONENT_CAPS_ADD.get(component)
    added = caps.get('add')
    if want_add is None:
        assert not added, f'unexpected capabilities.add on {component}: {added}'
    elif added is None:
        caps['add'] = list(want_add)
    else:
        assert added == want_add, f'conflicting capabilities.add on {component}: {added}'
    dropped = caps.get('drop')
    if dropped is None:
        caps['drop'] = ['ALL']
    else:
        assert dropped == ['ALL'], f'conflicting capabilities.drop on {component}: {dropped}'

    return expected


def render(src, dst, component):
    text = Path(src).read_text()
    before = parse(src)
    expected = expected_pod_doc(before, component)
    if before == expected:
        Path(dst).write_text(text)
    else:
        env = dict(os.environ, COMP=component)
        if component in COMPONENT_CAPS_ADD:
            expr = r'''
            .spec.securityContext.seccompProfile = {"type": "RuntimeDefault"} |
            (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.allowPrivilegeEscalation) = false |
            (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.capabilities.drop) = ["ALL"] |
            (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.capabilities.add) = ["DAC_OVERRIDE"]
            '''
        else:
            expr = r'''
            .spec.securityContext.seccompProfile = {"type": "RuntimeDefault"} |
            (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.allowPrivilegeEscalation) = false |
            (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.capabilities.drop) = ["ALL"]
            '''
        out = subprocess.check_output(['yq', expr, str(src)], env=env, text=True)
        Path(dst).write_text(out)
    verify(src, dst, component)


def verify(src, dst, component):
    before = parse(src)
    after = parse(dst)
    expected = expected_pod_doc(before, component)
    assert after == expected, f'{component} candidate changes fields outside securityContext hardening'


def health(pod_path, node, component, old_uid, expected_path):
    pod = json.loads(Path(pod_path).read_text())
    assert pod['metadata']['name'] == f'{component}-{node}', 'unexpected mirror pod name'
    assert not pod['metadata'].get('deletionTimestamp'), 'mirror pod is terminating'
    conditions = pod.get('status', {}).get('conditions', [])
    assert any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions), f'{component} pod not Ready'
    assert not old_uid or pod['metadata']['uid'] != old_uid, f'{component} mirror pod UID did not rotate'

    expected = parse(expected_path)
    c_actual = target_container(pod, component)
    c_expected = target_container(expected, component)
    assert c_actual.get('command') == c_expected.get('command'), f'{component} command drifted from manifest'
    exp_pod_sc = expected['spec'].get('securityContext', {})
    if exp_pod_sc.get('seccompProfile'):
        assert pod['spec'].get('securityContext', {}).get('seccompProfile') == exp_pod_sc['seccompProfile'], (
            f'{component} live pod seccompProfile mismatch'
        )
    exp_sc = c_expected.get('securityContext') or {}
    act_sc = c_actual.get('securityContext') or {}
    if 'allowPrivilegeEscalation' in exp_sc:
        assert act_sc.get('allowPrivilegeEscalation') is exp_sc['allowPrivilegeEscalation'], (
            f'{component} live allowPrivilegeEscalation mismatch'
        )
    if 'capabilities' in exp_sc:
        assert act_sc.get('capabilities') == exp_sc['capabilities'], (
            f'{component} live capabilities mismatch'
        )


if __name__ == '__main__':
    try:
        {
            'render': render,
            'verify': verify,
            'health': health,
        }[sys.argv[1]](*sys.argv[2:])
    except (AssertionError, KeyError, StopIteration, ValueError, subprocess.CalledProcessError) as exc:
        print(f'control-plane securityContext check failed: {exc}', file=sys.stderr)
        sys.exit(1)
