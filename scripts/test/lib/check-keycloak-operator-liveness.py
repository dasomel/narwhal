#!/usr/bin/env python3
"""R348: keycloak-operator livenessProbe patches /q/health/ready conservatively and idempotently."""
import copy
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
START_MARKER = '# D-KC-OPERATOR-LIVENESS:'
END_MARKER = 'echo "Waiting for Keycloak Operator deployment..."'


def extract_block(source: str) -> str:
    apply_pos = source.index('kubectl apply -n iam -f "$(manifest keycloak-kubernetes.yml)"')
    rollout_pos = source.index('kubectl rollout status deployment/keycloak-operator -n iam')
    start_pos = source.index(START_MARKER, apply_pos)
    end_pos = source.index(END_MARKER, start_pos)
    assert apply_pos < start_pos < end_pos < rollout_pos, \
        'D-KC-OPERATOR-LIVENESS patch must run after apply and before rollout status'
    return source[start_pos:end_pos]


def make_deployment(containers):
    return {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {'name': 'keycloak-operator', 'namespace': 'iam'},
        'spec': {'template': {'spec': {'containers': containers}}},
    }


def default_operator_container(name='keycloak-operator'):
    return {
        'name': name,
        'image': 'quay.io/keycloak/keycloak-operator:26.5.7',
        'livenessProbe': {
            'failureThreshold': 3,
            'httpGet': {'path': '/q/health/live', 'port': 8080, 'scheme': 'HTTP'},
            'initialDelaySeconds': 5,
            'periodSeconds': 10,
            'successThreshold': 1,
            'timeoutSeconds': 10,
        },
        'readinessProbe': {
            'failureThreshold': 3,
            'httpGet': {'path': '/q/health/ready', 'port': 8080, 'scheme': 'HTTP'},
            'initialDelaySeconds': 5,
            'periodSeconds': 10,
            'successThreshold': 1,
            'timeoutSeconds': 10,
        },
        'startupProbe': {
            'failureThreshold': 3,
            'httpGet': {'path': '/q/health/started', 'port': 8080, 'scheme': 'HTTP'},
            'initialDelaySeconds': 5,
            'periodSeconds': 10,
            'successThreshold': 1,
            'timeoutSeconds': 10,
        },
    }


STUB_KUBECTL = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ['R348_STATE'])
dep = json.loads(state_path.read_text())
args = sys.argv[1:]

def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v

if args[:3] == ['get', 'deployment', 'keycloak-operator']:
    if '-o' in args:
        fmt = args[args.index('-o') + 1]
        if fmt.startswith('jsonpath='):
            containers = dep['spec']['template']['spec']['containers']
            if '{.name}{"\\t"}{.image}' in fmt:
                for c in containers:
                    print(f"{c['name']}\t{c.get('image', '')}")
                sys.exit(0)
            if '{.name}' in fmt:
                for c in containers:
                    print(c['name'])
                sys.exit(0)
    print(json.dumps(dep))
    sys.exit(0)

if args[:3] == ['patch', 'deployment', 'keycloak-operator']:
    ptype = 'strategic'
    patch_str = None
    for i, a in enumerate(args):
        if a == '--type' and i + 1 < len(args):
            ptype = args[i + 1]
        elif a.startswith('--type='):
            ptype = a.split('=', 1)[1]
        elif a == '-p' and i + 1 < len(args):
            patch_str = args[i + 1]
        elif a.startswith('-p='):
            patch_str = a.split('=', 1)[1]
    assert patch_str is not None, f'missing -p in {args}'
    patch_obj = json.loads(patch_str)
    containers = dep['spec']['template']['spec']['containers']
    if ptype == 'strategic':
        p_containers = patch_obj['spec']['template']['spec']['containers']
        for pc in p_containers:
            name = pc.get('name')
            match = next((c for c in containers if c.get('name') == name), None)
            if match is None:
                sys.stderr.write(f'Error: container {name!r} has no image (strategic add rejected)\n')
                sys.exit(1)
            deep_merge(match, pc)
    elif ptype == 'json':
        for op in patch_obj:
            parts = [p for p in op['path'].split('/') if p]
            cur = dep
            for p in parts[:-1]:
                cur = cur[int(p)] if isinstance(cur, list) else cur[p]
            last = int(parts[-1]) if isinstance(cur, list) else parts[-1]
            cur[last] = op['value']
    else:
        raise AssertionError(f'unexpected patch type: {ptype}')
    state_path.write_text(json.dumps(dep))
    sys.exit(0)

raise AssertionError(f'unexpected kubectl args: {args}')
'''


def run_block(block: str, dep: dict, repeat: int = 1) -> dict:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        kubectl_bin = td_path / 'kubectl'
        kubectl_bin.write_text(STUB_KUBECTL)
        kubectl_bin.chmod(0o755)
        state_file = td_path / 'dep.json'
        state_file.write_text(json.dumps(copy.deepcopy(dep)))
        env = dict(os.environ, PATH=f'{td_path}:{os.environ["PATH"]}', R348_STATE=str(state_file))
        script = 'set -euo pipefail\n' + '\n'.join([block] * repeat)
        proc = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0, f'block failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}'
        return json.loads(state_file.read_text())


def assert_patched(container: dict):
    lp = container['livenessProbe']
    assert lp['httpGet'] == {'path': '/q/health/ready', 'port': 8080, 'scheme': 'HTTP'}, lp
    assert lp['periodSeconds'] == 30, lp
    assert lp['failureThreshold'] == 6, lp
    assert lp['timeoutSeconds'] == 10, lp
    assert lp['initialDelaySeconds'] == 5, lp
    assert lp['successThreshold'] == 1, lp
    assert container['startupProbe']['httpGet']['path'] == '/q/health/started', container['startupProbe']


def check(source: str):
    block = extract_block(source)
    # 1. Normal manifest, run twice for idempotency.
    dep1 = run_block(block, make_deployment([default_operator_container()]), repeat=2)
    assert_patched(dep1['spec']['template']['spec']['containers'][0])

    # 2. Sidecar at index 0, keycloak-operator at index 1.
    sidecar = {'name': 'istio-proxy', 'image': 'istio/proxyv2:1.25.0'}
    dep2 = run_block(block, make_deployment([sidecar, default_operator_container()]))
    assert dep2['spec']['template']['spec']['containers'][0] == sidecar, 'sidecar must be untouched'
    assert_patched(dep2['spec']['template']['spec']['containers'][1])

    # 3. Renamed container with keycloak-operator image: must not fail script.
    dep3 = run_block(block, make_deployment([default_operator_container(name='operator')]))
    assert_patched(dep3['spec']['template']['spec']['containers'][0])

    # 4. Completely unrecognized container shape: must not fail script.
    other = {'name': 'custom', 'image': 'busybox:1.36'}
    dep4 = run_block(block, make_deployment([other]))
    assert dep4['spec']['template']['spec']['containers'][0] == other


def main():
    source = (ROOT / 'scripts/cluster/11-keycloak.sh').read_text()
    check(source)

    mutations = {
        'removed kubectl patch call': (
            'kubectl patch deployment keycloak-operator -n iam',
            'true # kubectl patch deployment keycloak-operator -n iam',
        ),
        'aggressive failureThreshold=1': (
            '"failureThreshold": 6',
            '"failureThreshold": 1',
        ),
        'reverted path to /q/health/live': (
            '"path": "/q/health/ready"',
            '"path": "/q/health/live"',
        ),
        'aggressive periodSeconds=5': (
            '"periodSeconds": 30',
            '"periodSeconds": 5',
        ),
    }
    caught = 0
    for desc, (old, new) in mutations.items():
        assert old in source, f'mutation target missing in source: {old}'
        try:
            check(source.replace(old, new, 1))
        except (AssertionError, ValueError):
            caught += 1
        else:
            raise AssertionError(f'mutation survived: {desc}')
    print(f'ok: keycloak-operator livenessProbe (/q/health/ready, 30s*6=180s, timeout 10s, '
          f'idempotent, index/name safe); {caught} mutations caught')


if __name__ == '__main__':
    main()
