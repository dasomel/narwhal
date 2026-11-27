#!/usr/bin/env python3
"""Render, verify, and check live health for the bounded EventRateLimit edit (yq v4)."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CONFIG_PATH = '/etc/kubernetes/admission/admission-config.yaml'
CONFIG_FLAG = f'--admission-control-config-file={CONFIG_PATH}'
PLUGINS_BASE = 'NodeRestriction'
PLUGINS_TARGET = 'NodeRestriction,EventRateLimit'
PLUGINS_FLAG = f'--enable-admission-plugins={PLUGINS_TARGET}'
VOLUME_NAME = 'admission'
MOUNT_DIR = '/etc/kubernetes/admission'
EXPECTED_MOUNT = {'name': VOLUME_NAME, 'mountPath': MOUNT_DIR, 'readOnly': True}
EXPECTED_VOLUME = {
    'name': VOLUME_NAME,
    'hostPath': {'path': MOUNT_DIR, 'type': 'DirectoryOrCreate'},
}
EXPECTED_EXTRA_VOLUME = {
    'name': VOLUME_NAME,
    'hostPath': MOUNT_DIR,
    'mountPath': MOUNT_DIR,
    'readOnly': True,
    'pathType': 'DirectoryOrCreate',
}


def parse(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', '.', str(path)]))


def apiserver_container(doc):
    containers = [c for c in doc['spec']['containers'] if c.get('name') == 'kube-apiserver']
    assert len(containers) == 1, 'expected exactly one kube-apiserver container'
    return containers[0]


def expected_pod_doc(before):
    expected = copy.deepcopy(before)
    c = apiserver_container(expected)
    args = c.get('command')
    assert isinstance(args, list) and args and args[0] == 'kube-apiserver', 'unexpected kube-apiserver command'

    plugin_idx = [i for i, a in enumerate(args) if a == '--enable-admission-plugins' or a.startswith('--enable-admission-plugins=')]
    assert len(plugin_idx) <= 1, 'multiple --enable-admission-plugins flags'
    if plugin_idx:
        val = args[plugin_idx[0]]
        if val == f'--enable-admission-plugins={PLUGINS_BASE}':
            args[plugin_idx[0]] = PLUGINS_FLAG
        elif val != PLUGINS_FLAG:
            raise AssertionError(f'unexpected enable-admission-plugins value: {val}')
    else:
        args.append(PLUGINS_FLAG)

    cfg_idx = [i for i, a in enumerate(args) if a == '--admission-control-config-file' or a.startswith('--admission-control-config-file=')]
    assert len(cfg_idx) <= 1, 'multiple --admission-control-config-file flags'
    if cfg_idx:
        val = args[cfg_idx[0]]
        assert val == CONFIG_FLAG, f'admission-control-config-file points elsewhere: {val}'
    else:
        args.append(CONFIG_FLAG)

    mounts = c.setdefault('volumeMounts', [])
    assert isinstance(mounts, list), 'volumeMounts must be a list'
    mount_matches = [m for m in mounts if isinstance(m, dict) and (m.get('name') == VOLUME_NAME or m.get('mountPath') == MOUNT_DIR)]
    if not mount_matches:
        mounts.append(dict(EXPECTED_MOUNT))
    else:
        assert mount_matches == [EXPECTED_MOUNT], f'conflicting admission volumeMount: {mount_matches}'

    volumes = expected['spec'].setdefault('volumes', [])
    assert isinstance(volumes, list), 'volumes must be a list'
    vol_matches = [
        v for v in volumes
        if isinstance(v, dict) and (
            v.get('name') == VOLUME_NAME
            or (isinstance(v.get('hostPath'), dict) and v['hostPath'].get('path') == MOUNT_DIR)
        )
    ]
    if not vol_matches:
        volumes.append(copy.deepcopy(EXPECTED_VOLUME))
    else:
        assert vol_matches == [EXPECTED_VOLUME], f'conflicting admission volume: {vol_matches}'

    return expected


def _find_container_span(lines):
    """Return (start, end) line indices for the single kube-apiserver container item, or None."""
    specs = [i for i, line in enumerate(lines) if re.match(r'^spec:\s*(#.*)?$', line)]
    if len(specs) != 1:
        return None
    cont_idx = None
    cont_indent = None
    for i in range(specs[0] + 1, len(lines)):
        m = re.match(r'^(\s*)containers:\s*(#.*)?$', lines[i])
        if m:
            cont_idx = i
            cont_indent = len(m.group(1))
            break
    if cont_idx is None:
        return None

    # Locate sequence items under spec.containers:
    item_starts = []
    item_indent = None
    for i in range(cont_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent <= cont_indent and not line[cont_indent:].startswith('-'):
            end_containers = i
            break
        m = re.match(r'^(\s*)-\s', line)
        if m and (item_indent is None or len(m.group(1)) == item_indent):
            if item_indent is None:
                if len(m.group(1)) < cont_indent:
                    end_containers = i
                    break
                item_indent = len(m.group(1))
            item_starts.append(i)
    else:
        end_containers = len(lines)

    if not item_starts:
        return None
    spans = [(item_starts[k], item_starts[k + 1] if k + 1 < len(item_starts) else end_containers) for k in range(len(item_starts))]
    matches = [
        span for span in spans
        if any(re.match(r'^\s*(?:-\s+)?name:\s*["\']?kube-apiserver["\']?\s*(#.*)?$', lines[j]) for j in range(span[0], span[1]))
    ]
    return matches[0] if len(matches) == 1 else None


def _render_lines(text, before):
    """Best-effort comment/indent-preserving block edit for standard kubeadm manifests."""
    lines = text.splitlines(keepends=True)
    span = _find_container_span(lines)
    if span is None:
        return None

    c_before = apiserver_container(before)
    args_before = c_before['command']

    # 1. Edit command lines inside the kube-apiserver container span.
    cmd_starts = [i for i in range(span[0], span[1]) if re.fullmatch(r'\s*- kube-apiserver\s*\n?', lines[i])]
    if len(cmd_starts) != 1:
        return None
    last_arg = cmd_starts[0]
    plugin_line = None
    for i in range(cmd_starts[0] + 1, span[1]):
        if re.match(r'^\s*- --', lines[i]):
            last_arg = i
            if re.match(rf'^\s*- ["\']?--enable-admission-plugins={PLUGINS_BASE}["\']?\s*\n?$', lines[i]):
                plugin_line = i
        else:
            break
    if last_arg == cmd_starts[0]:
        return None
    arg_indent = re.match(r'^(\s*)', lines[last_arg]).group(1)

    has_plugin = any(a.startswith('--enable-admission-plugins=') for a in args_before)
    has_cfg = any(a.startswith('--admission-control-config-file=') for a in args_before)

    if plugin_line is not None:
        lines[plugin_line] = f'{arg_indent}- {PLUGINS_FLAG}\n'
    elif not has_plugin and not has_cfg:
        lines.insert(last_arg + 1, f'{arg_indent}- {PLUGINS_FLAG}\n')
        lines.insert(last_arg + 2, f'{arg_indent}- {CONFIG_FLAG}\n')
        has_plugin = has_cfg = True
    if not has_plugin:
        lines.insert(last_arg + 1, f'{arg_indent}- {PLUGINS_FLAG}\n')
        last_arg += 1
    if not has_cfg:
        lines.insert(last_arg + 1, f'{arg_indent}- {CONFIG_FLAG}\n')

    # Recompute container span after command insertions.
    span = _find_container_span(lines)
    if span is None:
        return None

    # 2. Insert volumeMount if missing.
    if not any(m.get('name') == VOLUME_NAME for m in c_before.get('volumeMounts', [])):
        vm_headers = [i for i in range(span[0], span[1]) if re.match(r'^\s*volumeMounts:\s*(#.*)?$', lines[i])]
        if len(vm_headers) != 1:
            return None
        vm_head = vm_headers[0]
        vm_key_indent = len(re.match(r'^(\s*)', lines[vm_head]).group(1))
        vm_item_indent = None
        vm_end = vm_head + 1
        for i in range(vm_head + 1, span[1]):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith('#'):
                continue
            ind = len(lines[i]) - len(lines[i].lstrip(' '))
            if ind < vm_key_indent or (ind == vm_key_indent and not lines[i].lstrip(' ').startswith('- ')):
                vm_end = i
                break
            if vm_item_indent is None and lines[i].lstrip(' ').startswith('- '):
                vm_item_indent = ' ' * ind
            vm_end = i + 1
        if vm_item_indent is None:
            return None
        sub = vm_item_indent + '  '
        block = [
            f'{vm_item_indent}- mountPath: {MOUNT_DIR}\n',
            f'{sub}name: {VOLUME_NAME}\n',
            f'{sub}readOnly: true\n',
        ]
        lines[vm_end:vm_end] = block

    # 3. Insert spec.volumes entry if missing.
    if not any(v.get('name') == VOLUME_NAME for v in before['spec'].get('volumes', [])):
        vol_headers = [i for i, line in enumerate(lines) if re.match(r'^  volumes:\s*(#.*)?$', line)]
        if len(vol_headers) != 1:
            return None
        vh = vol_headers[0]
        vol_item_indent = None
        vol_end = vh + 1
        for i in range(vh + 1, len(lines)):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith('#'):
                continue
            ind = len(lines[i]) - len(lines[i].lstrip(' '))
            if ind < 2 or (ind == 2 and not lines[i].lstrip(' ').startswith('- ')):
                vol_end = i
                break
            if vol_item_indent is None and lines[i].lstrip(' ').startswith('- '):
                vol_item_indent = ' ' * ind
            vol_end = i + 1
        if vol_item_indent is None:
            return None
        sub = vol_item_indent + '  '
        sub2 = sub + '  '
        block = [
            f'{vol_item_indent}- hostPath:\n',
            f'{sub2}path: {MOUNT_DIR}\n',
            f'{sub2}type: DirectoryOrCreate\n',
            f'{sub}name: {VOLUME_NAME}\n',
        ]
        lines[vol_end:vol_end] = block

    return ''.join(lines)


def _render_yq(src, dst):
    env = dict(
        os.environ,
        PLUGINS_BASE_FLAG=f'--enable-admission-plugins={PLUGINS_BASE}',
        PLUGINS_TARGET_FLAG=PLUGINS_FLAG,
        CFG_FLAG=CONFIG_FLAG,
        VOL_NAME=VOLUME_NAME,
        MOUNT_DIR=MOUNT_DIR,
    )
    expr = r'''
    (.spec.containers[] | select(.name == "kube-apiserver") | .command) |= (
      (if any_c(. == strenv(PLUGINS_BASE_FLAG))
       then map(if . == strenv(PLUGINS_BASE_FLAG) then strenv(PLUGINS_TARGET_FLAG) else . end)
       elif any_c(. == strenv(PLUGINS_TARGET_FLAG)) then .
       else . + [strenv(PLUGINS_TARGET_FLAG)] end)
      | (if any_c(. == strenv(CFG_FLAG)) then . else . + [strenv(CFG_FLAG)] end)
    ) |
    (.spec.containers[] | select(.name == "kube-apiserver") | .volumeMounts) |= (
      (. // []) | if any_c(.name == strenv(VOL_NAME)) then . else . + [{"name": strenv(VOL_NAME), "mountPath": strenv(MOUNT_DIR), "readOnly": true}] end
    ) |
    .spec.volumes |= (
      (. // []) | if any_c(.name == strenv(VOL_NAME)) then . else . + [{"name": strenv(VOL_NAME), "hostPath": {"path": strenv(MOUNT_DIR), "type": "DirectoryOrCreate"}}] end
    )
    '''
    out = subprocess.check_output(['yq', expr, str(src)], env=env, text=True)
    Path(dst).write_text(out)


def render(src, dst):
    text = Path(src).read_text()
    before = parse(src)
    expected = expected_pod_doc(before)
    if before == expected:
        Path(dst).write_text(text)
    else:
        candidate = _render_lines(text, before)
        if candidate is not None:
            Path(dst).write_text(candidate)
            try:
                if parse(dst) != expected:
                    _render_yq(src, dst)
            except subprocess.CalledProcessError:
                _render_yq(src, dst)
        else:
            _render_yq(src, dst)
    verify(src, dst)


def verify(src, dst):
    before = parse(src)
    after = parse(dst)
    expected = expected_pod_doc(before)
    assert after == expected, 'candidate changes fields outside the EventRateLimit edit'


def expected_cluster_config(before):
    assert before.get('apiVersion') == 'kubeadm.k8s.io/v1beta4' and before.get('kind') == 'ClusterConfiguration', (
        'expected kubeadm.k8s.io/v1beta4 ClusterConfiguration'
    )
    expected = copy.deepcopy(before)
    api_server = expected.setdefault('apiServer', {})
    assert isinstance(api_server, dict), 'apiServer must be a mapping'

    extra_args = api_server.setdefault('extraArgs', [])
    assert isinstance(extra_args, list), 'apiServer.extraArgs must be a list'

    p_idx = [i for i, a in enumerate(extra_args) if isinstance(a, dict) and a.get('name') == 'enable-admission-plugins']
    assert len(p_idx) <= 1, 'multiple enable-admission-plugins entries in ClusterConfiguration'
    if p_idx:
        val = extra_args[p_idx[0]].get('value')
        if val == PLUGINS_BASE:
            extra_args[p_idx[0]]['value'] = PLUGINS_TARGET
        elif val != PLUGINS_TARGET:
            raise AssertionError(f'unexpected enable-admission-plugins in ClusterConfiguration: {val}')
    else:
        extra_args.append({'name': 'enable-admission-plugins', 'value': PLUGINS_TARGET})

    c_idx = [i for i, a in enumerate(extra_args) if isinstance(a, dict) and a.get('name') == 'admission-control-config-file']
    assert len(c_idx) <= 1, 'multiple admission-control-config-file entries in ClusterConfiguration'
    if c_idx:
        val = extra_args[c_idx[0]].get('value')
        assert val == CONFIG_PATH, f'admission-control-config-file in ClusterConfiguration points elsewhere: {val}'
    else:
        extra_args.append({'name': 'admission-control-config-file', 'value': CONFIG_PATH})

    extra_vols = api_server.setdefault('extraVolumes', [])
    assert isinstance(extra_vols, list), 'apiServer.extraVolumes must be a list'
    v_matches = [v for v in extra_vols if isinstance(v, dict) and (v.get('name') == VOLUME_NAME or v.get('mountPath') == MOUNT_DIR)]
    if not v_matches:
        extra_vols.append(dict(EXPECTED_EXTRA_VOLUME))
    else:
        assert v_matches == [EXPECTED_EXTRA_VOLUME], f'conflicting admission extraVolume in ClusterConfiguration: {v_matches}'

    return expected


def render_config(src, dst):
    text = Path(src).read_text()
    before = parse(src)
    expected = expected_cluster_config(before)
    if before == expected:
        Path(dst).write_text(text)
    else:
        env = dict(
            os.environ,
            PLUGINS_BASE=PLUGINS_BASE,
            PLUGINS_TARGET=PLUGINS_TARGET,
            CONFIG_PATH=CONFIG_PATH,
            VOL_NAME=VOLUME_NAME,
            MOUNT_DIR=MOUNT_DIR,
        )
        expr = r'''
        .apiServer.extraArgs |= (
          (. // [])
          | (if any_c(.name == "enable-admission-plugins")
             then map(if .name == "enable-admission-plugins" then .value = strenv(PLUGINS_TARGET) else . end)
             else . + [{"name": "enable-admission-plugins", "value": strenv(PLUGINS_TARGET)}] end)
          | (if any_c(.name == "admission-control-config-file")
             then .
             else . + [{"name": "admission-control-config-file", "value": strenv(CONFIG_PATH)}] end)
        ) |
        .apiServer.extraVolumes |= (
          (. // [])
          | if any_c(.name == strenv(VOL_NAME))
            then .
            else . + [{"name": strenv(VOL_NAME), "hostPath": strenv(MOUNT_DIR), "mountPath": strenv(MOUNT_DIR), "readOnly": true, "pathType": "DirectoryOrCreate"}]
            end
        )
        '''
        out = subprocess.check_output(['yq', expr, str(src)], env=env, text=True)
        Path(dst).write_text(out)
    verify_config(src, dst)


def verify_config(src, dst):
    before = parse(src)
    after = parse(dst)
    expected = expected_cluster_config(before)
    assert after == expected, 'ClusterConfiguration changes fields outside the EventRateLimit edit'


def health(pod_path, node, old_uid, expected_path):
    pod = json.loads(Path(pod_path).read_text())
    assert pod['metadata']['name'] == f'kube-apiserver-{node}', 'unexpected pod name'
    assert not pod['metadata'].get('deletionTimestamp'), 'pod is terminating'
    conditions = pod.get('status', {}).get('conditions', [])
    assert any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions), 'pod not Ready'
    assert not old_uid or pod['metadata']['uid'] != old_uid, 'mirror pod UID did not rotate'

    expected = parse(expected_path)
    c_actual = apiserver_container(pod)
    c_expected = apiserver_container(expected)
    assert c_actual.get('command') == c_expected.get('command'), 'running command does not match expected manifest'

    if EXPECTED_MOUNT in c_expected.get('volumeMounts', []):
        assert any(
            m.get('name') == VOLUME_NAME and m.get('mountPath') == MOUNT_DIR and m.get('readOnly') is True
            for m in c_actual.get('volumeMounts', [])
        ), 'admission volumeMount missing or not readOnly'
    if EXPECTED_VOLUME in expected['spec'].get('volumes', []):
        assert any(
            v.get('name') == VOLUME_NAME
            and isinstance(v.get('hostPath'), dict)
            and v['hostPath'].get('path') == MOUNT_DIR
            and v['hostPath'].get('type') == 'DirectoryOrCreate'
            for v in pod['spec'].get('volumes', [])
        ), 'admission hostPath volume missing'


if __name__ == '__main__':
    try:
        {
            'render': render,
            'verify': verify,
            'render-config': render_config,
            'verify-config': verify_config,
            'health': health,
        }[sys.argv[1]](*sys.argv[2:])
    except (AssertionError, KeyError, StopIteration, ValueError, subprocess.CalledProcessError) as exc:
        print(f'admission verification failed: {exc}', file=sys.stderr)
        sys.exit(1)
