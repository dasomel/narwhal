#!/usr/bin/env python3
"""R342: execute the real Vagrantfile with a recording DSL; never load Vagrant."""
import json
import os
import subprocess
import tempfile
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DSL = r'''
require "json"
class DSL
  attr_reader :values, :providers, :provisions, :machines
  def initialize
    @values, @providers, @provisions, @machines = {}, {}, [], {}
  end
  def vm; self; end
  def vmx; @values; end
  def define(name, **opts)
    @machines[name] = node = DSL.new
    yield node
  end
  def provider(name)
    @providers[name] ||= DSL.new
    yield @providers[name], DSL.new
  end
  def provision(*args, **opts); @provisions << opts; end
  # Registering a trigger must not execute its host/guest commands.
  def before(*args)
    trigger = DSL.new
    yield trigger
    @record_trigger = trigger if trigger.values["name"] == "Record Narwhal topology"
  end
  def ruby(&block); @callback = block; end
  def fire_record; @record_trigger.fire if @record_trigger; end
  def fire; @callback.call(nil, nil); end
  def after(*args); end
  def method_missing(name, *args, **opts)
    if name.to_s.end_with?("=")
      @values[name.to_s.delete_suffix("=")] = args.first
    else
      self
    end
  end
end
module Vagrant
  def self.has_plugin?(*args); false; end
  def self.configure(*args)
    @config = DSL.new
    yield @config
  end
  def self.config; @config; end
end
load ARGV.fetch(0)
Vagrant.config.fire_record if ENV["R342_RECORD"] == "1"
puts JSON.generate(Vagrant.config.machines.transform_values { |m|
  {providers: m.providers.transform_values(&:values), provisions: m.provisions}
})
'''


def run(src, env=None, ids=(), nfs=False, record=None, create=False):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'Vagrantfile').write_text(src)
        (root / 'dsl.rb').write_text(DSL)
        for name, provider, content in ids:
            path = root / '.vagrant/machines' / name / provider / 'id'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        if record is not None:
            path = root / '.vagrant/narwhal-topology'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record + '\n')
        if nfs:
            path = root / '.vagrant/narwhal-nfs/narwhal-nfs.vmdk'
            path.parent.mkdir(parents=True)
            path.touch()
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(('NARWHAL_', 'AIRGAP'))}
        proc = subprocess.run(['ruby', str(root / 'dsl.rb'), str(root / 'Vagrantfile')],
                              env=dict(clean, R342_RECORD="1" if create else "0", **(env or {})), text=True,
                              capture_output=True, timeout=15)
        record_path = root / '.vagrant/narwhal-topology'
        proc.record = record_path.read_text().strip() if record_path.exists() else None
        proc.stdout = proc.stdout.replace(tmp, '<root>')
        return proc


def definitions(src, env=None, ids=()):
    proc = run(src, env, ids)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def check(src):
    ha = definitions(src)
    assert list(ha) == ['master-1', 'master-2', 'master-3',
                        'worker-1', 'worker-2', 'worker-3'], 'default fleet changed'
    assert definitions(src, {'NARWHAL_TOPOLOGY': 'ha'}) == ha
    assert definitions(src, {'NARWHAL_TOPOLOGY': ''}) == ha, 'empty must mean ha'
    live = [('master-1', 'vmware_desktop', 'fake-id')]
    for env in ({}, {'NARWHAL_TOPOLOGY': ''}):
        proc = run(src, env, live, record='single')
        assert proc.returncode == 0 and list(json.loads(proc.stdout)) == ['master-1'], proc.stderr
    for requested, recorded in [('ha', 'single'), ('single', 'ha')]:
        proc = run(src, {'NARWHAL_TOPOLOGY': requested}, live, record=recorded)
        assert proc.returncode != 0 and all(v in proc.stderr for v in
               (requested, recorded, 'Destroy', 'recorded topology')), proc.stderr
    proc = run(src, record='single')
    assert proc.returncode == 0 and json.loads(proc.stdout) == ha and proc.record is None
    proc = run(src, {'NARWHAL_TOPOLOGY': 'single'}, create=True)
    assert proc.returncode == 0 and proc.record == 'single', proc.stderr
    for name, node in ha.items():
        cpu, memory = (2, 6144) if name.startswith('master') else (4, 8192)
        assert node['providers']['vmware_desktop']['numvcpus'] == cpu
        assert node['providers']['vmware_desktop']['memsize'] == memory
        assert node['providers']['virtualbox']['cpus'] == cpu
        assert node['providers']['virtualbox']['memory'] == memory
    for extra, cpu, memory in [({}, 10, 24576),
                               ({'NARWHAL_SINGLE_CPUS': '9',
                                 'NARWHAL_SINGLE_MEMORY_MB': '24576'}, 9, 24576)]:
        nodes = definitions(src, dict(NARWHAL_TOPOLOGY='single', **extra))
        assert list(nodes) == ['master-1'], 'single must define exactly master-1'
        node = nodes['master-1']
        assert node['providers']['vmware_desktop']['numvcpus'] == cpu
        assert node['providers']['vmware_desktop']['memsize'] == memory
        assert node['providers']['virtualbox']['cpus'] == cpu
        assert node['providers']['virtualbox']['memory'] == memory
        for script in ('02-init-cluster.sh', '06-phase2-start.sh'):
            provision = next(p for p in node['provisions'] if p.get('path', '').endswith(script))
            assert provision['env'].get('NARWHAL_TOPOLOGY') == 'single'
            assert provision['env']['MASTER_COUNT'] == 1
        boot = next(p for p in node['provisions'] if p.get('path', '').endswith('06-boot-heal-install.sh'))
        assert boot['env']['NARWHAL_TOPOLOGY'] == 'single'
        cni = next(p for p in node['provisions'] if p.get('path', '').endswith('03-cni-install.sh'))
        assert cni['env'].get('NARWHAL_TOPOLOGY') == 'single'
        assert node['providers']['vmware_desktop']['nvme0:1.present'] == 'TRUE'

    for key, values in [('NARWHAL_TOPOLOGY', ('bogus',)),
                        ('NARWHAL_SINGLE_CPUS', ('0', '-1', '1.5', 'abc')),
                        ('NARWHAL_SINGLE_MEMORY_MB', ('0', '-1', '1.5', 'abc'))]:
        for value in values:
            proc = run(src, {'NARWHAL_TOPOLOGY': 'single', key: value})
            assert proc.returncode != 0 and key in proc.stderr, f'{key}={value} accepted'
    ids = [('master-2', 'vmware_desktop', '/fake/master.vmx'),
           ('worker-1', 'virtualbox', 'fake-id')]
    proc = run(src, {'NARWHAL_TOPOLOGY': 'single'}, ids)
    assert proc.returncode != 0, 'topology mismatch accepted'
    assert all(s in proc.stderr for s in ('master-2', 'worker-1', 'Destroy', 'switch topology'))
    definitions(src, ids=ids)  # allowed HA machines
    definitions(src, {'NARWHAL_TOPOLOGY': 'single'},
                [('worker-1', 'virtualbox', '')])  # empty residual id is not created
    proc = run(src, {'NARWHAL_NFS_DISK_GB': '0'}, nfs=True)
    assert proc.returncode != 0 and 'NFS data disk' in proc.stderr, 'NFS guard weakened'


def check_guest(source):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lib = root / 'scripts/common/lib-topology.sh'
        lib.parent.mkdir(parents=True)
        record = root / 'guest-topology'
        lib.write_text(source.replace('/etc/narwhal-topology', str(record)))
        clean = {k: v for k, v in os.environ.items() if k != 'NARWHAL_TOPOLOGY'}

        def shell(code, topology=None):
            env = dict(clean)
            if topology is not None:
                env['NARWHAL_TOPOLOGY'] = topology
            return subprocess.run(['bash', '-ec', code], env=env, cwd=root,
                                  capture_output=True, text=True, timeout=10)

        resolve = f'source "{lib}"; printf "%s|%s" "$TOPOLOGY_NODES" "$TOPOLOGY_MASTERS"'
        assert shell(resolve).stdout == '6|3'
        for recorded, count in [('single', '1|1'), ('ha', '6|3')]:
            record.write_text(recorded + '\n')
            for topology in (None, '', recorded):
                proc = shell(resolve, topology)
                assert proc.returncode == 0 and proc.stdout == count, proc.stderr
            proc = shell(resolve, 'ha' if recorded == 'single' else 'single')
            assert proc.returncode != 0 and f'recorded topology {recorded}' in proc.stderr
        record.write_text('invalid\n')
        assert shell(resolve).returncode != 0
        record.unlink()
        # Execute the actual installer prefix twice, without installing units or touching /etc.
        installer = (ROOT / 'scripts/common/06-boot-heal-install.sh').read_text()
        prefix = installer.split('echo "=== Boot-Heal Install ==="', 1)[0]
        prefix = prefix.replace('/home/vagrant/scripts/common/lib-topology.sh', str(lib))
        prefix = prefix.replace('/etc/narwhal-topology', str(record))
        for _ in range(2):
            proc = shell('chown() { printf "%s" "$*"; };\n' + prefix, 'single')
            assert proc.returncode == 0, proc.stderr
            assert record.read_text() == 'single\n' and record.stat().st_mode & 0o777 == 0o644
            assert proc.stdout == f'root:root {record}'
        assert shell(prefix, 'ha').returncode != 0
        assert record.read_text() == 'single\n'
        # Guest entrypoints must resolve before doing work (including installed healer).
        for name in ('scripts/cluster/cluster-heal.sh', 'scripts/cluster/06-phase2-start.sh',
                     'scripts/common/06-boot-heal-install.sh', 'scripts/test/verify-cluster.sh',
                     'scripts/gitops/push-to-gitea.sh', 'scripts/gitops/apply-topology.sh'):
            source_text = (ROOT / name).read_text()
            line = next(line for line in source_text.splitlines()
                        if line.startswith('source ') and 'lib-topology.sh' in line)
            line = line.replace('/home/vagrant/scripts/common/lib-topology.sh', str(lib))
            entrypoint = root / name
            entrypoint.parent.mkdir(parents=True, exist_ok=True)
            code = f'REPO_ROOT="{root}"; ' + line + '; printf "%s" "$NARWHAL_TOPOLOGY"'
            proc = subprocess.run(['bash', '-ec', code, str(entrypoint)], env=clean,
                                  capture_output=True, text=True, timeout=10)
            assert proc.returncode == 0 and proc.stdout == 'single', (name, proc.stderr)
        # An active host fleet takes precedence over a guest fallback.
        path = root / '.vagrant/machines/master-1/virtualbox/id'
        path.parent.mkdir(parents=True)
        path.write_text('fake-id')
        (root / '.vagrant/narwhal-topology').write_text('ha\n')
        assert shell(resolve).stdout == '6|3'


def check_shell():
    # Execute the host driver's configuration prefix, before any VM/disk operation.
    driver = (ROOT / 'scripts/up.sh').read_text().split('# D-VM-DISK-GUARD:', 1)[0]
    driver = driver.replace('check_topology_machines', ':')
    driver = driver.replace('$(dirname "$0")/common/lib-topology.sh',
                            str(ROOT / 'scripts/common/lib-topology.sh'))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lib = root / 'scripts/common/lib-topology.sh'
        lib.parent.mkdir(parents=True)
        lib.write_text((ROOT / 'scripts/common/lib-topology.sh').read_text())
        code = f'source "{lib}"; check_topology_machines; printf "%s" "$ALL_VMS"'
        clean = {k: v for k, v in os.environ.items() if k != 'NARWHAL_TOPOLOGY'}
        def shell(topology=None):
            env = dict(clean)
            if topology is not None:
                env['NARWHAL_TOPOLOGY'] = topology
            return subprocess.run(['bash', '-ec', code], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=10)
        for topology in (None, ''):
            proc = shell(topology)
            assert proc.returncode == 0 and 'worker-3' in proc.stdout
        path = root / '.vagrant/machines/master-1/virtualbox/id'
        path.parent.mkdir(parents=True)
        path.write_text('fake-id')
        record = root / '.vagrant/narwhal-topology'
        record.write_text('single\n')
        for topology in (None, '', 'single'):
            proc = shell(topology)
            assert proc.returncode == 0 and proc.stdout == 'master-1', proc.stderr
        proc = shell('ha')
        assert proc.returncode != 0 and 'recorded topology single' in proc.stderr
        record.unlink()
        path = root / '.vagrant/machines/worker-1/virtualbox/id'
        path.parent.mkdir(parents=True)
        path.write_text('fake-id')
        assert shell('single').returncode != 0
        assert shell('ha').returncode == 0
    cni = (ROOT / 'scripts/cluster/03-cni-install.sh').read_text()
    args = cni[cni.index('      local topology_args=()'):cni.index('      cilium install --version')]
    for topology, expected in [('single', '--set operator.replicas=1'), ('ha', '')]:
        proc = subprocess.run(['bash', '-c', 'f() {\n' + args +
                               '\nprintf "%s" "${topology_args[*]-}"; }; f'],
                              env=dict(os.environ, NARWHAL_TOPOLOGY=topology),
                              capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0 and proc.stdout == expected
    # Keep the driver-prefix check independent of this checkout's live host record.
    isolated = tempfile.TemporaryDirectory()
    isolated_lib = Path(isolated.name) / 'scripts/common/lib-topology.sh'
    isolated_lib.parent.mkdir(parents=True)
    driver = driver.replace(str(ROOT / 'scripts/common/lib-topology.sh'), str(isolated_lib))
    isolated_lib.write_text((ROOT / 'scripts/common/lib-topology.sh').read_text())
    phase2 = (ROOT / 'scripts/cluster/06-phase2-start.sh').read_text()
    taint = phase2[phase2.index('# D-SINGLE-NODE:'):phase2.index('# Phase 2 scripts')]
    for topology, expected, count in [('ha', 'master-1 master-2 master-3 worker-1 worker-2 worker-3', '6'),
                                      ('single', 'master-1', '1')]:
        env = {k: v for k, v in os.environ.items() if k not in ('EXPECTED_NODES', 'NARWHAL_TOPOLOGY')}
        env['NARWHAL_TOPOLOGY'] = topology
        proc = subprocess.run(['bash', '-c', driver + '\nprintf "%s|%s" "$ALL_VMS" "$EXPECTED_NODES"'],
                              env=env, capture_output=True, text=True, cwd=ROOT, timeout=10)
        assert proc.returncode == 0 and proc.stdout == f'{expected}|{count}', proc.stderr
        proc = subprocess.run(['bash', '-c', 'kubectl() { echo "$*"; };\n' + taint],
                              env=env, capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0, proc.stderr
        if topology == 'single':
            assert 'control-plane:NoSchedule-' in proc.stdout
            assert '--overwrite' not in proc.stdout
    isolated.cleanup()


def main():
    src = (ROOT / 'Vagrantfile').read_text()
    check(src)
    check_shell()
    resolver = (ROOT / 'scripts/common/lib-topology.sh').read_text()
    check_guest(resolver)
    for name, old, new in [
        ('guest-record-read', 'recorded_topology="$(cat /etc/narwhal-topology)"', 'recorded_topology=""'),
        ('guest-contradiction', '[ "${NARWHAL_TOPOLOGY}" != "${recorded_topology}" ]', 'false'),
    ]:
        assert old in resolver, name
        try:
            check_guest(resolver.replace(old, new))
        except AssertionError:
            continue
        raise AssertionError(f'mutation survived: {name}')
    print('ok: guest record resolution, conflicts, installer rerun/mode/owner command; 2 mutations caught')
    mutations = [
        ('empty-ha', 'REQUESTED_TOPOLOGY.empty? ? (RECORDED_TOPOLOGY || "ha")', 'false ? (RECORDED_TOPOLOGY || "ha")'),
        ('record-read', 'File.read(TOPOLOGY_RECORD).strip : nil', '"ha" : nil'),
        ('record-contradiction', 'REQUESTED_TOPOLOGY != RECORDED_TOPOLOGY', 'false'),
        ('record-write', 'File.write(TOPOLOGY_RECORD, "#{TOPOLOGY}\\n")', 'nil'),
        ('default-count', 'TOPOLOGY == "single" ? 1 : 3', 'TOPOLOGY == "single" ? 1 : 2'),
        ('single-workers', 'TOPOLOGY == "single" ? 0 : 3', 'TOPOLOGY == "single" ? 1 : 3'),
        ('ha-sizing', 'MASTER_CPUS = 2', 'MASTER_CPUS = 3'),
        ('single-sizing', '"NARWHAL_SINGLE_CPUS", "10"', '"NARWHAL_SINGLE_CPUS", "4"'),
        ('invalid-topology', '%w[ha single]', '%w[ha single bogus]'),
        ('mismatch-guard', 'unless UNDEFINED_MACHINES.empty?', 'if false'),
        ('memory-validation', 'SINGLE_MEMORY >= 1', 'SINGLE_MEMORY >= 0'),
        ('phase2-env', '"NARWHAL_TOPOLOGY" => TOPOLOGY', '"IGNORED_TOPOLOGY" => TOPOLOGY'),
        ('nfs-guard', 'if NFS_DISK_GB == 0 && NFS_DISK_RECORDED', 'if false && NFS_DISK_RECORDED'),
    ]
    for name, old, new in mutations:
        assert old in src, f'mutation anchor missing: {name}'
        try:
            check(src.replace(old, new))
        except AssertionError:
            continue
        raise AssertionError(f'mutation survived: {name}')
    runpy.run_path(str(ROOT / 'scripts/test/lib/check-single-node-gitops.py'))['main']()
    print(f'ok: HA 3+3 unchanged; single 1+0; validation/guards; {len(mutations)} mutations caught')


if __name__ == '__main__':
    main()
