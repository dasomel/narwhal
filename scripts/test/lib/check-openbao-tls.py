#!/usr/bin/env python3
"""R390: offline ownership/parity/policy checks and bash 3.2 behavioral fixtures."""
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
    boot = BOOT.read_text()
    values = yaml.safe_load(boot.split("cat > /tmp/openbao-values.yaml <<'EOF'\n")[1].split('\nEOF')[0])['server']
    cert = yaml.safe_load(boot.split('apiVersion: cert-manager.io/v1\n')[1].split('\nEOF')[0])['spec']
    return app, values, cert


def check(app, boot, cert):
    assert cert['duration'] == '8760h' and cert['renewBefore'] == '720h'
    for key in ('shareProcessNamespace', 'updateStrategyType', 'extraContainers', 'volumes'):
        assert app[key] == boot[key], key
    assert app['shareProcessNamespace'] is True
    assert app['updateStrategyType'] == 'OnDelete'
    side, = app['extraContainers']
    security = side['securityContext']
    assert security['runAsUser'] == 100 and security['runAsNonRoot']
    assert security['readOnlyRootFilesystem'] and not security['allowPrivilegeEscalation']
    assert security['capabilities']['drop'] == ['ALL']
    assert security['seccompProfile']['type'] == 'RuntimeDefault'
    assert side['volumeMounts'] == [{'name': 'tls-reload-cert', 'mountPath': '/tls', 'readOnly': True}]
    volume, = [v for v in app['volumes'] if v['name'] == 'tls-reload-cert']
    assert volume['secret'] == {'secretName': 'openbao-tls', 'items': [{'key': 'tls.crt', 'path': 'tls.crt'}]}
    image = yaml.safe_load_all((ROOT / 'gitops/resources/openbao-backup.yaml').read_text())
    assert side['image'] in str(list(image))  # Existing mirrored, digest-pinned OpenSSL image.
    script = side['command'][2]
    for text in ('set -o pipefail', '/tls/tls.crt', 'timeout 10 openssl s_client', '"$mounted" != "$served"',
                 'kill -HUP', "sed -n '2p'", 'sleep 30'):
        assert text in script, text
    assert 'kubectl' not in script and 'tls.key' not in script
    return script


def run_fixture(shell, script, detector=False):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        def exe(name, body):
            p = d / name
            p.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
            p.chmod(0o755)
        exe('openssl', '''if [ "$1" = x509 ]; then
  case " $* " in *" -in "*) cat "$MOUNTED";; *) cat;; esac
elif [ "$1" = base64 ]; then cat
elif [ "$1" = s_client ]; then [ "$MODE" != error ] || exit 1; cat "$SERVED"
else exit 1; fi''')
        exe('timeout', 'shift; exec "$@"')
        exe('sleep', '''n=$(cat "$COUNT"); n=$((n+1)); echo "$n" > "$COUNT"
if [ "$n" -ge 3 ] && [ "$DETECTOR" = no ]; then exit 73; fi
if [ "$MODE" = converge ]; then cp "$MOUNTED" "$SERVED"; fi''')
        exe('kubectl', '''case " $* " in
*" get secret "*) cat "$MOUNTED";;
*" exec "*) if [ "$MODE" = error ]; then exit 1; fi; cat "$SERVED";;
*) exit 1;; esac''')
        proc = d / 'proc/123'
        proc.mkdir(parents=True)
        (proc / 'comm').write_text('bao\n')
        (proc / 'cmdline').write_bytes(b'/bin/bao\x00server\x00-config=x\x00')
        other = d / 'proc/124'
        other.mkdir()
        (other / 'comm').write_text('bao\n')
        (other / 'cmdline').write_bytes(b'bao\x00status\x00')
        env = dict(os.environ, PATH=tmp + ':' + os.environ['PATH'], MOUNTED=str(d/'mounted'),
                   SERVED=str(d/'served'), COUNT=str(d/'count'), SIGNALS=str(d/'signals'),
                   DETECTOR='yes' if detector else 'no')
        code = script if detector else script.replace('/proc/[0-9]*', tmp+'/proc/[0-9]*')
        # Mock the signal boundary, never send a real signal from a test.
        if not detector:
            code = 'kill() { echo "$*" >> "$SIGNALS"; }\n' + code
        cases = [('same', 'A', 'A', 0), ('stale', 'A', 'B', 1),
                 ('converge', 'A', 'B', 0), ('empty', '', '', 1), ('error', 'A', 'B', 1)]
        for mode, mounted, served, want in cases:
            (d/'mounted').write_text(mounted)
            (d/'served').write_text(served)
            (d/'count').write_text('0')
            (d/'signals').write_text('')
            env['MODE'] = mode
            result = subprocess.run([shell, '-c', code, 'fixture', '/explicit/kubeconfig'], env=env,
                                    capture_output=True, text=True, timeout=10)
            if detector:
                assert result.returncode == want, (mode, result.stdout, result.stderr)
            else:
                assert result.returncode == 73, (mode, result.stderr)
                signals = (d/'signals').read_text().splitlines()
                expected = {'same': 0, 'stale': 3, 'converge': 1, 'empty': 0, 'error': 0}[mode]
                assert len(signals) == expected, (mode, signals, expected)
                assert all(s == '-HUP 123' for s in signals)


def main():
    app, boot, cert = inputs()
    script = check(app, boot, cert)
    # R390 intentionally does not call Helm or promtool.
    for shell in dict.fromkeys(['/bin/bash', 'bash']):
        run_fixture(shell, script)
        run_fixture(shell, VERIFY.read_text(), detector=True)
    if '--mutation-verify' in sys.argv:
        mutations = [
            lambda a, b, c: c.pop('duration'),
            lambda a, b, c: c.update(renewBefore='1h'),
            lambda a, b, c: a.update(shareProcessNamespace=False),
            lambda a, b, c: a.update(updateStrategyType='RollingUpdate'),
            lambda a, b, c: a['extraContainers'][0]['securityContext'].update(runAsUser=10100),
            lambda a, b, c: a['extraContainers'][0]['securityContext'].update(readOnlyRootFilesystem=False),
            lambda a, b, c: a['volumes'][0]['secret'].pop('items'),
            lambda a, b, c: a['extraContainers'][0]['command'].__setitem__(2, script.replace('kill -HUP', 'kill -TERM')),
        ]
        for mutate in mutations:
            a, b, c = copy.deepcopy((app, boot, cert))
            mutate(a, b, c)
            try:
                check(a, b, c)
            except (AssertionError, KeyError):
                continue
            raise AssertionError('mutation survived')
        # Prove behavioral assertions reject an always-signal implementation too.
        try:
            run_fixture('/bin/bash', script.replace('[ "$mounted" != "$served" ]', 'true'))
        except AssertionError:
            pass
        else:
            raise AssertionError('always-SIGHUP mutation survived')
        print('R390: 9 mutations rejected; mounted/live convergence and verifier fixtures passed (/bin/bash + bash)')
    else:
        print('R390: static and behavioral checks passed')


if __name__ == '__main__':
    main()
