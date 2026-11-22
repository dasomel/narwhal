#!/usr/bin/env python3
"""R390: in-container watcher behavior, ownership/parity and external verifier."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'gitops/charts/narwhal-apps/templates/openbao.yaml'
BOOT = ROOT / 'scripts/cluster/08-4-storage.sh'
VERIFY = ROOT / 'scripts/test/verify-openbao-tls.sh'


def inputs():
    app = yaml.safe_load(APP.read_text())['spec']['source']['helm']['valuesObject']['server']
    text = BOOT.read_text()
    boot = yaml.safe_load(text.split("cat > /tmp/openbao-values.yaml <<'EOF'\n")[1].split('\nEOF')[0])['server']
    cert = yaml.safe_load(text.split('apiVersion: cert-manager.io/v1\n')[1].split('\nEOF')[0])['spec']
    return app, boot, cert


def check(app, boot, cert):
    assert cert['duration'] == '8760h' and cert['renewBefore'] == '720h'
    for key in ('extraArgs', 'updateStrategyType', 'volumes', 'volumeMounts'):
        assert app[key] == boot[key], key
    for values in (app, boot):
        assert 'shareProcessNamespace' not in values
        assert not values.get('extraContainers')
        assert not values.get('postStart')
        assert values['updateStrategyType'] == 'OnDelete'
        assert [v['name'] for v in values['volumes']] == ['userconfig-openbao-tls', 'tmp']
        assert 'valkey' not in str(values) and 'tls-reload-cert' not in str(values)
    script = app['extraArgs']
    assert script.startswith('& bao_server_pid=$!; (')
    assert '\n' not in script  # Pinned chart does not indent interpolated newlines.
    assert script.endswith('wait "$bao_server_pid"')
    assert 'openssl' not in script and 'kill -HUP 1' not in script
    return script


def watcher_fixture(script):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        proc = d / 'proc'
        proc.mkdir()
        for pid, comm, argv in [(1, 'bao', b'bao\0server\0'),
                                (22, 'bao', b'bao\0server\0'),
                                (13, 'bao', b'bao\0status\0'),
                                (14, 'sh', b'bao\0server\0')]:
            p = proc / str(pid)
            p.mkdir()
            (p / 'comm').write_text(comm + '\n')
            (p / 'cmdline').write_bytes(argv)
        (d / 'old').write_text('certificate A')
        (d / 'new').write_text('certificate B')
        env = dict(os.environ, FIXTURE=str(d))
        body = script.split('bao_server_pid=$!; (', 1)[1].rsplit(') & wait', 1)[0]
        body = body.replace('/proc/[0-9]*', str(proc) + '/[0-9]*')
        body = body.replace('/openbao/tls/tls.crt', str(d / 'cert'))
        prelude = '''tick=0
kill() {
  if [ "$MODE" = denied ] && [ "$tick" = 1 ]; then return 1; fi
  echo "$*" >> "$FIXTURE/signals"
}
sleep() {
  [ "$1" = 30 ] || exit 98
  tick=$((tick+1))
  [ "$tick" -le 4 ] || return 1
  if [ "$MODE" != same ] && [ "$tick" = 1 ]; then
    ln -s "$FIXTURE/new" "$FIXTURE/next"
    mv -f "$FIXTURE/next" "$FIXTURE/cert"
  fi
  if [ "$MODE" = missing ] && [ "$tick" = 2 ]; then
    echo bao > "$FIXTURE/proc/22/comm"
  fi
}
'''
        for mode, expected in [('same', []), ('change', ['-HUP 22']), ('missing', ['-HUP 22']), ('denied', ['-HUP 22'])]:
            cert = d / 'cert'
            cert.unlink(missing_ok=True)
            cert.symlink_to(d / 'old')
            (d / 'signals').write_text('')
            (proc / '22/comm').write_text('absent\n' if mode == 'missing' else 'bao\n')
            result = subprocess.run(['/bin/sh', '-c', prelude + body], env=dict(env, MODE=mode),
                                    capture_output=True, text=True, timeout=5)
            signals = (d / 'signals').read_text().splitlines()
            assert result.returncode == 0, result.stderr
            assert signals == expected, (mode, signals, expected)
            assert result.stdout.count('sent SIGHUP') == len(expected)
            if mode in ('missing', 'denied'):
                assert 'ERROR: OpenBao server PID missing' in result.stderr
        # The same wrapper still returns the server status if the watcher crashes.
        wrapped = 'server() { sleep 0.1; return 17; }\nserver ' + script
        wrapped = wrapped.replace('sha256sum /openbao/tls/tls.crt', 'false')
        result = subprocess.run(['/bin/sh', '-ec', wrapped], capture_output=True, timeout=5)
        assert result.returncode == 17, result


def verifier_fixture(shell):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        def exe(name, body):
            p = d / name
            p.write_text('#!/bin/sh\n' + body + '\n')
            p.chmod(0o755)
        exe('sleep', '[ "$1" = 10 ] && exec /bin/sleep 10; exec /bin/sleep 0.01')
        exe('kubectl', '''case " $* " in
*" get secret "*)
 n=$(cat "$FIXTURE/count"); n=$((n+1)); echo "$n" > "$FIXTURE/count"
 [ "$MODE" != api ] || [ "$n" -gt 1 ] || exit 1
 echo A;;
*" port-forward "*)
 [ "$MODE" != forward ] || [ "$(cat "$FIXTURE/count")" -gt 1 ] || exit 1
 echo 'Forwarding from 127.0.0.1:18200 -> 8200'; exec /bin/sleep 10;;
*) exit 1;; esac''')
        exe('openssl', '''case "$1" in
base64) cat;;
x509) if [ "$2" = -in ]; then cat "$3"; else cat; fi;;
s_client)
 n=$(cat "$FIXTURE/count")
 [ "$MODE" != connect ] || [ "$n" -gt 1 ] || exit 1
 case "$MODE" in empty) :;; stale) echo B;; error) exit 1;; *) echo A;; esac;;
*) exit 1;; esac''')
        env = dict(os.environ, PATH=tmp + ':' + os.environ['PATH'], FIXTURE=tmp)
        for mode, rc, attempts in [('same', 0, 1), ('api', 0, 2), ('connect', 0, 2),
                                   ('forward', 0, 2), ('stale', 1, 7), ('error', 1, 7), ('empty', 1, 7)]:
            (d / 'count').write_text('0')
            result = subprocess.run([shell, str(VERIFY), '/explicit/kubeconfig'],
                                    env=dict(env, MODE=mode), capture_output=True, timeout=15)
            assert result.returncode == rc, (mode, result.stdout, result.stderr)
            assert int((d / 'count').read_text()) == attempts, mode


def main():
    app, boot, cert = inputs()
    script = check(app, boot, cert)
    watcher_fixture(script)
    for shell in dict.fromkeys(['/bin/bash', 'bash']):
        verifier_fixture(shell)
    if '--mutation-verify' in sys.argv:
        static = [lambda a, b, c: c.pop('duration'),
                  lambda a, b, c: c.update(renewBefore='1h'),
                  lambda a, b, c: a.update(shareProcessNamespace=True),
                  lambda a, b, c: a.update(extraContainers=[{'image': 'valkey'}]),
                  lambda a, b, c: a.update(updateStrategyType='RollingUpdate'),
                  lambda a, b, c: b.update(extraArgs='')]
        for mutate in static:
            args = copy.deepcopy((app, boot, cert))
            mutate(*args)
            try:
                check(*args)
            except (AssertionError, KeyError):
                continue
            raise AssertionError('static mutation survived')
        mutations = [
            ('[ "$current_hash" != "$last_hash" ]', 'true'),
            ('last_hash=$current_hash', ':'),
            ('found=false', 'last_hash=$current_hash; found=false'),
            ('[ "${proc##*/}" != 1 ] || continue', ':'),
            ('[ "$(cat "$proc/comm" 2>/dev/null)" = bao ] || continue', ':'),
            ('''[ "$(tr '\\000' '\\n' < "$proc/cmdline" 2>/dev/null | sed -n '2p')" = server ] || continue''', ':'),
            ('kill -HUP "${proc##*/}"', 'kill -HUP 1'),
        ]
        for old, new in mutations:
            assert old in script, old
            try:
                watcher_fixture(script.replace(old, new))
            except AssertionError:
                continue
            raise AssertionError('behavior mutation survived: ' + old)
        print(f'R390: {len(static) + len(mutations)} mutations rejected; /bin/sh watcher and bash verifier fixtures passed')
    else:
        print('R390: static and behavioral checks passed')


if __name__ == '__main__':
    main()
