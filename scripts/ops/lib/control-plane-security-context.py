#!/usr/bin/env python3
"""Render, verify, and health-check control-plane static Pod securityContext edits (yq v4)."""
import copy
from datetime import datetime
import json
import re
import os
from pathlib import Path
import subprocess
import sys

# D-CP-ETCD-DEFER: etcd is excluded from static-pod securityContext patching after the
# 2026-11-28 live trial timed out on etcd without root cause established (an offline ctr run
# with cap-drop ALL succeeded with and without DAC_OVERRIDE). Only kube-scheduler,
# kube-controller-manager, and kube-apiserver are patched here.
# Cost: etcd KSV001/KSV003/KSV004 remain actionable until live root-cause analysis completes.
# Escape hatch: add etcd back once a live node trial proves its startup requirements.
COMPONENTS = ('kube-scheduler', 'kube-controller-manager', 'kube-apiserver')
# D-CP-APISERVER-FILECAP: the kube-apiserver image binary carries cap_net_bind_service=ep
# (getcap, 2026-11-28), so exec fails with EPERM once NET_BIND_SERVICE leaves the bounding set.
# Must match scripts/ops/lib/kubeadm-patches/*+strategic.yaml (R426 checks parity).
ADD_CAPS = {'kube-apiserver': ['NET_BIND_SERVICE']}


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
    added = caps.get('add')
    want_add = ADD_CAPS.get(component, [])
    if not added:
        if want_add:
            caps['add'] = list(want_add)
    else:
        assert added == want_add, f'unexpected capabilities.add on {component}: {added}'
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
        expr = r'''
        .spec.securityContext.seccompProfile = {"type": "RuntimeDefault"} |
        (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.allowPrivilegeEscalation) = false |
        (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.capabilities.drop) = ["ALL"]
        '''
        if ADD_CAPS.get(component):
            env['ADDCAPS'] = json.dumps(ADD_CAPS[component])
            expr += r''' |
        (.spec.containers[] | select(.name == strenv(COMP)) | .securityContext.capabilities.add) = (strenv(ADDCAPS) | from_json)
        '''
        out = subprocess.check_output(['yq', expr, str(src)], env=env, text=True)
        Path(dst).write_text(out)
    verify(src, dst, component)


def verify(src, dst, component):
    before = parse(src)
    after = parse(dst)
    expected = expected_pod_doc(before, component)
    assert after == expected, f'{component} candidate changes fields outside securityContext hardening'


def crictl_health(inspect_path, component, old_cid, expected_path):
    """Verify node-side crictl inspect JSON against expected manifest without API access."""
    assert component in COMPONENTS, f'unsupported component: {component}'
    data = json.loads(Path(inspect_path).read_text())
    status = data.get('status') or {}
    assert status.get('state') == 'CONTAINER_RUNNING', f'{component} container not running: {status.get("state")}'
    meta = status.get('metadata') or {}
    assert meta.get('name') == component, f'unexpected container name: {meta.get("name")}'
    actual_cid = status.get('id') or ''
    assert actual_cid, f'{component} inspect missing status.id'
    assert not old_cid or actual_cid != old_cid, f'{component} container ID did not rotate ({actual_cid})'

    expected = parse(expected_path)
    c_expected = target_container(expected, component)
    expected_argv = list(c_expected.get('command') or []) + list(c_expected.get('args') or [])

    info = data.get('info') or {}
    runtime_spec = info.get('runtimeSpec')
    cri_sc = (((info.get('config') or {}).get('linux') or {}).get('security_context'))

    if isinstance(runtime_spec, dict) and expected_argv:
        proc_args = (runtime_spec.get('process') or {}).get('args')
        if proc_args is not None:
            assert proc_args == expected_argv, f'{component} runtime argv drifted from manifest'

    exp_pod_sc = expected['spec'].get('securityContext') or {}
    exp_sc = c_expected.get('securityContext') or {}
    needs_sc_check = bool(
        exp_pod_sc.get('seccompProfile')
        or 'allowPrivilegeEscalation' in exp_sc
        or 'capabilities' in exp_sc
    )
    if not needs_sc_check:
        return

    assert isinstance(runtime_spec, dict) or isinstance(cri_sc, dict), (
        f'{component} crictl inspect missing both info.runtimeSpec and info.config.linux.security_context'
    )

    if exp_pod_sc.get('seccompProfile') == {'type': 'RuntimeDefault'}:
        if isinstance(runtime_spec, dict):
            seccomp = (runtime_spec.get('linux') or {}).get('seccomp')
            assert isinstance(seccomp, dict) and seccomp.get('defaultAction'), (
                f'{component} runtimeSpec missing active RuntimeDefault seccomp profile'
            )
        if isinstance(cri_sc, dict):
            cri_seccomp = cri_sc.get('seccomp')
            if isinstance(cri_seccomp, dict) and 'profile_type' in cri_seccomp:
                assert cri_seccomp['profile_type'] in (0, 'RuntimeDefault', 'RUNTIME_DEFAULT'), (
                    f'{component} CRI seccomp profile_type mismatch: {cri_seccomp}'
                )

    if exp_sc.get('allowPrivilegeEscalation') is False:
        if isinstance(runtime_spec, dict):
            assert (runtime_spec.get('process') or {}).get('noNewPrivileges') is True, (
                f'{component} runtimeSpec.process.noNewPrivileges is not true'
            )
        if isinstance(cri_sc, dict):
            assert cri_sc.get('no_new_privs') is True, (
                f'{component} CRI security_context.no_new_privs is not true'
            )

    exp_caps = exp_sc.get('capabilities') or {}
    if exp_caps.get('drop') == ['ALL'] and exp_caps.get('add'):
        want = sorted('CAP_' + str(c).upper() for c in exp_caps['add'])
        if isinstance(runtime_spec, dict):
            proc_caps = (runtime_spec.get('process') or {}).get('capabilities')
            assert isinstance(proc_caps, dict), f'{component} runtimeSpec.process.capabilities missing'
            for cap_set in ('bounding', 'permitted'):
                vals = sorted(proc_caps.get(cap_set) or [])
                assert vals == want, f'{component} runtimeSpec {cap_set} {vals} != {want}'
            assert not (proc_caps.get('ambient') or []), f'{component} runtimeSpec retained ambient capabilities'
    if exp_caps.get('drop') == ['ALL'] and not exp_caps.get('add'):
        if isinstance(runtime_spec, dict):
            proc_caps = (runtime_spec.get('process') or {}).get('capabilities')
            assert isinstance(proc_caps, dict), f'{component} runtimeSpec.process.capabilities missing'
            for cap_set in ('bounding', 'effective', 'permitted', 'inheritable', 'ambient'):
                vals = proc_caps.get(cap_set) or []
                assert not vals, f'{component} runtimeSpec retained capabilities in {cap_set}: {vals}'
        if isinstance(cri_sc, dict):
            cri_caps = cri_sc.get('capabilities') or {}
            dropped = [str(c).upper() for c in (cri_caps.get('drop_capabilities') or [])]
            added = cri_caps.get('add_capabilities') or []
            assert 'ALL' in dropped and not added, (
                f'{component} CRI capabilities mismatch: drop={dropped} add={added}'
            )


def parse_rfc3339_ns(value):
    """containerd CreatedAt is RFC 3339 with nanoseconds ('...39.699806236Z'); Python 3.10's
    fromisoformat accepts at most microseconds, so parse the fraction separately."""
    m = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})', value)
    assert m, f'unparseable timestamp: {value}'
    base = datetime.fromisoformat(m.group(1) + ('+00:00' if m.group(3) == 'Z' else m.group(3)))
    frac = (m.group(2) or '').ljust(9, '0')[:9]
    return int(base.timestamp()) * 1_000_000_000 + int(frac)


def ctr_health(inspect_path, component, old_cid, expected_path):
    """Verify ctr info for the running container and its creation after manifest write."""
    assert component in COMPONENTS, f'unsupported component: {component}'
    data = json.loads(Path(inspect_path).read_text())
    actual_cid = data.get('_ctrId') or ''
    assert actual_cid, f'{component} ctr info missing container id'
    assert not old_cid or actual_cid != old_cid, f'{component} container ID did not rotate ({actual_cid})'
    spec = data.get('Spec') or {}
    process = spec.get('process') or {}
    # D-CP-HEALTH-EXPECTED: compare with the manifest being verified (hardened candidate OR the
    # restored original), not with a fixed "no capabilities" rule — the hardened apiserver keeps
    # NET_BIND_SERVICE and the restored original keeps runtime defaults (both false negatives seen live).
    c_expected = target_container(parse(expected_path), component)
    exp_sc = c_expected.get('securityContext') or {}
    exp_caps = exp_sc.get('capabilities') or {}
    if exp_sc.get('allowPrivilegeEscalation') is False:
        assert process.get('noNewPrivileges') is True, f'{component} noNewPrivileges is not true'
    if exp_caps.get('drop') == ['ALL']:
        want = sorted('CAP_' + str(c).upper() for c in (exp_caps.get('add') or []))
        caps = process.get('capabilities') or {}
        for cap_set in ('bounding', 'effective', 'permitted'):
            got = sorted(caps.get(cap_set) or [])
            assert got == want, f'{component} {cap_set} capabilities {got} != {want}'
    if (parse(expected_path)['spec'].get('securityContext') or {}).get('seccompProfile') == {'type': 'RuntimeDefault'}:
        assert (spec.get('linux') or {}).get('seccomp'), f'{component} ctr spec missing seccomp'
    created = data.get('CreatedAt')
    assert created, f'{component} ctr info missing CreatedAt'
    created_ns = parse_rfc3339_ns(created)
    assert created_ns > int(data.get('_manifestMtimeNs') or 0), f'{component} container predates manifest write'


def auto_health(inspect_path, component, old_cid, expected_path):
    data = json.loads(Path(inspect_path).read_text())
    if '_ctrId' in data:
        ctr_health(inspect_path, component, old_cid, expected_path)
    else:
        crictl_health(inspect_path, component, old_cid, expected_path)


def health(pod_path, node, component, old_uid, expected_path):
    """Non-blocking API mirror-pod check (supplemental report only)."""
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
            'crictl-health': crictl_health,
            'ctr-health': ctr_health,
            'auto-health': auto_health,
            'health': health,
        }[sys.argv[1]](*sys.argv[2:])
    except (AssertionError, KeyError, StopIteration, ValueError, subprocess.CalledProcessError) as exc:
        print(f'control-plane securityContext check failed: {exc}', file=sys.stderr)
        sys.exit(1)
