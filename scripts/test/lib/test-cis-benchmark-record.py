#!/usr/bin/env python3
"""Behavioral jq transform contract; expected counts come from live trial, not code."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILTER = ROOT / 'scripts/ops/cis-benchmark-record.jq'
FIXTURE = ROOT / 'scripts/test/fixtures/cis-benchmark/cis-1.23.json'
META = {'schemaVersion': 1, 'runId': 'fixture', 'tool': 'kube-bench', 'toolVersion': '0.16.0',
        'image': 'docker.io/aquasec/kube-bench:v0.16.0@sha256:75506f222d1eb6ce2a751a5533bdc0a3b54c898e2e49e7751d0ee22cfb862679',
        'benchmarkId': 'cis-1.23', 'node': 'narwhal-master-1', 'nodeRoles': ['control-plane'],
        'kubernetesVersion': 'fixture-not-a-runtime-version',
        'startedAt': '2026-11-24T01:00:00Z', 'finishedAt': '2026-11-24T01:01:00Z'}


def transform(raw):
  return subprocess.run(['jq', '-ce', '--argjson', 'meta', json.dumps(META), '-f', str(FILTER)],
                        input=json.dumps(raw), text=True, capture_output=True)


def main():
  source = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE
  raw = json.loads(source.read_text())
  p = transform(raw)
  assert p.returncode == 0, p.stderr
  record = json.loads(p.stdout)
  assert record['totals'] == {'pass': 74, 'fail': 5, 'warn': 46, 'info': 0}
  assert {r['controlId'] for r in record['results'] if r['status'] == 'FAIL'} == {
    '1.1.12', '1.2.6', '1.3.7', '1.4.2', '4.2.6'}
  assert len(record['results']) == 125
  assert len(p.stdout.encode()) < 900000
  assert not any(k in p.stdout for k in ('"audit":', '"remediation":', '"test_info":'))
  assert all(len(r['actualValue']) <= 400 for r in record['results'])
  assert all(r['type'] in ('automated', 'manual') for r in record['results'])
  print(f'PASS: fixture {source.name}: 74/5/46/0, five FAIL ids, 125 results; record bytes={len(p.stdout.encode())}')
  result = raw['Controls'][0]['tests'][0]['results'][0]
  for value in ['--token=do-not-publish', '--password do-not-publish', '--client-key=/secret/key',
                'Bearer do-not-publish', 'https://user:do-not-publish@example.org',
                '-----BEGIN PRIVATE KEY-----\ndo-not-publish', 'eyJhbGciOiJIUzI1NiJ9.payload.sig',
                'x' * 500 + '--secret=do-not-publish']:
    raw['Controls'][0]['tests'][0]['section'] = value
    result['actual_value'] = value
    result['expected_result'] = value
    result['reason'] = value
    out = transform(raw)
    assert out.returncode == 0, out.stderr
    item = json.loads(out.stdout)['results'][0]
    assert all(item[k] == '[REDACTED]' for k in ('section', 'actualValue', 'expectedResult', 'reason'))
    assert 'do-not-publish' not in out.stdout
  result['actual_value'] = 'x' * 501
  assert len(json.loads(transform(raw).stdout)['results'][0]['actualValue']) == 400
  raw['Controls'][0]['tests'][0]['section'] = 'x' * 501
  assert len(json.loads(transform(raw).stdout)['results'][0]['section']) == 400
  for value in (None, 42, {}, []):
    raw['Controls'][0]['tests'][0]['section'] = value
    assert transform(raw).returncode != 0
  raw['Controls'][0]['tests'][0]['section'] = '1.1'
  raw['Controls'][0]['version'] = 'cis-1.12'
  assert transform(raw).returncode != 0
  raw['Controls'][0]['version'] = 'cis-1.23'
  raw['Totals']['total_pass'] = 999
  assert transform(raw).returncode != 0
  raw['Controls'] = []
  assert transform(raw).returncode != 0
  print('PASS: redaction (8 cases, before truncation), truncation, section type, benchmark/totals mismatch, empty output rejected')


if __name__ == '__main__':
  main()
