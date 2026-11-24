#!/usr/bin/env python3
"""Round-trip the stdlib emitter with an independent YAML parser, when available."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('renderer', ROOT / 'scripts/ops/render-cis-benchmark.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
try:
  import yaml
except ImportError:
  print('SKIP: CIS YAML round trips (PyYAML unavailable)')
  raise SystemExit(0)

strings = ['', '한글', 'plain', 'a:b', 'a: b', '#', 'a # b', '0123', '1', '1e3', '0xFF', '0o12', '12:34', '2026-11-24', '10m', '32Mi', '17 2 * * *',
           'a\u0085b', 'a\u2028b', 'true', 'FALSE', 'null', 'Yes', 'on', 'NO', '~', '.nan', '+1',
           ' leading', 'trailing ', 'one\ntwo', ' leading\nnext\n',
           'one\n\n', '\n', '\n\n', '  first\n\nlast\n\n', '---', '?key',
           '[key]', '{key}', '*alias', '&anchor', 'a:', 'quoted"key']
assert renderer.emit({'kind': 'Application', 'cpu': '10m'}) == 'kind: Application\ncpu: 10m\n'
for obj in ({}, []):
  assert yaml.safe_load(renderer.emit(obj)) == obj
for value in strings:
  for obj in ({'value': value}, {'nested': [{'value': value}]}, {value: 'value'} if '\n' not in value else {}):
    if obj:
      assert yaml.safe_load(renderer.emit(obj)) == obj, (obj, renderer.emit(obj))
for bad in ({'a\nb': 'value'}, {'value': 'a\r\nb'}, {1: 'value'}):
  try:
    renderer.emit(bad)
  except (ValueError, TypeError):
    pass
  else:
    raise AssertionError(f'unsupported value silently accepted: {bad!r}')
for nodes in ([], [{'name': 'control', 'roles': ['control-plane']}, {'name': 'worker', 'roles': ['worker']}]):
  for source in (renderer.render(nodes), renderer.render_app(nodes)):
    docs = list(yaml.safe_load_all(source))
    assert docs and all(d is not None for d in docs)
    for doc in docs:
      assert yaml.safe_load(renderer.emit(doc)) == doc
if shutil.which('helm'):
  output = subprocess.check_output(['helm', 'template', 'test', 'gitops/charts/narwhal-apps'], cwd=ROOT, text=True)
  assert all(d is not None for d in yaml.safe_load_all(output)), 'empty Helm YAML document'
  print('PASS: helm template narwhal-apps has no empty documents')
else:
  print('SKIP: Helm empty-document assertion (helm unavailable)')
print('PASS: CIS real manifests/adversarial YAML round trips; unsupported inputs rejected')
