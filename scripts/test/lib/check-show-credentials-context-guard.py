#!/usr/bin/env python3
"""R341: show-credentials.sh must identify a Narwhal cluster before any Secret read.

D-CRED-CONTEXT-CHECK: run the real script against a stub kubectl on PATH (no cluster,
no secrets). Cost: one temp dir per case; escape hatch: live-run the script by hand.
"""

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/test/show-credentials.sh'
MARKER = 'clusterissuer/narwhal-ca-issuer'

STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
  *"get --raw /readyz"*) exit 0 ;;
  *"config current-context"*) echo wrong-cluster; exit 0 ;;
  *"get ${STUB_MARKER}"*) [ "$STUB_NARWHAL" = 1 ] && exit 0 || exit 1 ;;
esac
exit 0
"""


def run(path, narwhal, args=()):
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp, 'kubectl')
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        log = Path(tmp, 'calls')
        env = dict(os.environ, PATH=f'{tmp}:{os.environ["PATH"]}', STUB_LOG=str(log),
                   STUB_NARWHAL='1' if narwhal else '0', STUB_MARKER=MARKER)
        proc = subprocess.run(['bash', str(path), *args], env=env, capture_output=True,
                              text=True, timeout=60)
        calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def check(path):
    src = path.read_text()
    body = src.index('\nrow() {')  # skip the get_secret helper definition above it
    first_secret = min(m.start() for m in re.finditer(r'get_secret |get secret ', src[body:])) + body
    guard = src.find(MARKER)
    assert guard != -1, 'no Narwhal identity marker in show-credentials.sh'
    assert guard < first_secret, 'identity guard must run before the first Secret read'

    for args in ((), ('--context', 'other')):
        proc, calls = run(path, False, args)
        assert proc.returncode != 0, f'non-Narwhal cluster accepted ({args})'
        assert not any('secret' in c for c in calls), f'Secret read on non-Narwhal cluster ({args})'
        assert 'not a Narwhal cluster' in proc.stdout, 'refusal must say why'
        assert '--context' in proc.stdout and 'KUBECONFIG' in proc.stdout, 'refusal must say how to fix'
        if args:
            assert "context 'other'" in proc.stdout, 'refusal must name the explicit context'
            assert any('--context other' in c and MARKER in c for c in calls), \
                'explicit --context must be validated'
        else:
            assert "context 'wrong-cluster'" in proc.stdout, 'refusal must name the current context'

    proc, calls = run(path, True)
    assert proc.returncode == 0, f'happy path failed: {proc.stdout[-300:]}'
    assert any('secret' in c for c in calls), 'happy path must still read Secrets'
    ids = [i for i, c in enumerate(calls) if MARKER in c]
    secrets = [i for i, c in enumerate(calls) if 'secret' in c]
    assert ids and ids[0] < secrets[0], 'identity probe must precede the first Secret read'


def mutate(src, kind):
    if kind == 'guard-removed':
        return re.sub(r'(?s)NARWHAL_CLUSTER_MARKER=.*?\nfi\n', '', src, count=1)
    if kind == 'guard-after-secret':
        m = re.search(r'(?s)(# D-CRED-CONTEXT.*?\nfi\n)', src)
        block = m.group(1)
        src = src.replace(block, '')
        anchor = 'KC_ADMIN_USER=$(get_secret'
        return src.replace(anchor, block + anchor, 1)
    if kind == 'no-exit':
        return re.sub(r'(?s)(Refusing to read.*?)exit 1', r'\1true', src, count=1)
    raise ValueError(kind)


def main():
    check(SCRIPT)
    src = SCRIPT.read_text()
    for kind in ('guard-removed', 'guard-after-secret', 'no-exit'):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp, 'show-credentials.sh')
            bad.write_text(mutate(src, kind))
            try:
                check(bad)
            except AssertionError:
                continue
        raise SystemExit(f'mutation not caught: {kind}')
    print('ok: guard precedes Secret reads; 3 mutations caught')


if __name__ == '__main__':
    main()
