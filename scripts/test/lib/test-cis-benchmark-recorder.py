#!/usr/bin/env python3
"""Exercise recorder phase/error behavior with a fake Kubernetes API client."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main():
  with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    for d in ('work', 'tmp', 'auth', 'scripts', 'bin'):
      (base / d).mkdir()
    (base / 'auth/token').write_text('fake-token')
    (base / 'auth/ca.crt').write_text('fake-ca')
    (base / 'scripts/record.jq').write_text((ROOT / 'scripts/ops/cis-benchmark-record.jq').read_text())
    shell = (ROOT / 'scripts/ops/cis-benchmark-recorder.sh').read_text()
    for part in ('tmp', 'work', 'auth', 'scripts'):
      shell = shell.replace('/' + part, str(base / part))
    script = base / 'recorder.sh'
    script.write_text(shell)
    client = base / 'bin/kubectl'
    client.write_text('''#!/bin/bash
set -euo pipefail
case "$1" in
  config) exit 0 ;;
  version) echo '{"serverVersion":{"gitVersion":"v1.35.fixture"}}' ;;
  patch)
    for arg in "$@"; do
      case "$arg" in --patch-file=*) cp "${arg#*=}" "${PUBLISHED}" ;; esac
    done ;;
  *) exit 2 ;;
esac
''')
    client.chmod(0o755)
    env = {**os.environ, 'PATH': str(base / 'bin') + ':' + os.environ['PATH'],
           'PUBLISHED': str(base / 'published.json'), 'NODE_NAME': 'test-node', 'NODE_ROLES': 'worker',
           'RECORD_NAME': 'test-record', 'POD_UID': 'test-pod', 'BENCH_IMAGE': 'test-image',
           'KUBERNETES_SERVICE_HOST': 'fake-api', 'KUBERNETES_SERVICE_PORT': '443'}

    def run(*args):
      return subprocess.run(['bash', str(script), *args], env=env, capture_output=True, text=True)

    def record():
      return json.loads(json.loads((base / 'published.json').read_text())['data']['record.json'])

    for name, data, code in (
      ('success', (ROOT / 'scripts/test/fixtures/cis-benchmark/cis-1.23.json').read_text(), '0'),
      ('nonzero-exit', '{}', '2'), ('malformed-json', '{broken', '0'),
      ('empty-json', '{}', '0'), ('missing-exit', '{}', None),
    ):
      assert run('begin').returncode == 0
      pending = record()
      assert pending['status'] == 'error' and pending['finishedAt'] is None and pending['results'] == []
      (base / 'work/raw.json').write_text(data)
      exit_file = base / 'work/exit-code'
      if code is None:
        exit_file.unlink(missing_ok=True)
      else:
        exit_file.write_text(code)
      out = run()
      result = record()
      assert result['finishedAt'] is not None
      if name == 'success':
        assert out.returncode == 0 and result['status'] == 'complete'
        assert 'reason' not in result and result['totals']['pass'] == 74
      else:
        assert out.returncode == 1 and result['status'] == 'error'
        assert result['results'] == [] and result['totals'] is None
    print('PASS: recorder 5 behavioral cases (success/nonzero/malformed/empty/missing exit), error-before-scan')


if __name__ == '__main__':
  main()
