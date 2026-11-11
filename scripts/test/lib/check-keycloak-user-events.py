#!/usr/bin/env python3
"""R340: offline regression for retained user events, including existing realms."""

import json
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def check(source):
    # D-647-CHECK: inspect the unconditional CLI command, not a cluster. Cost:
    # this proves wiring only; escape hatch: verify live events after provisioning.
    commands = source.replace('\\\n', '').splitlines()
    matches = [line for line in commands if re.match(
        r'^kc_exec update "realms/\$\{REALM\}/events/config" ', line)]
    assert len(matches) == 1, 'event configuration must be an unconditional update'
    command = matches[0]
    args = shlex.split(command)
    settings = {}
    for i in range(3, len(args), 2):
        assert args[i] == '-s', 'expected typed kcadm settings'
        key, value = args[i + 1].split('=', 1)
        assert key not in settings, 'duplicate event setting'
        settings[key] = json.loads(value)
    assert settings == {
        'eventsEnabled': True,
        'eventsExpiration': 2592000,
        'enabledEventTypes': ['LOGIN', 'LOGIN_ERROR', 'LOGOUT'],
    }, 'only bounded user events must be configured; leave admin settings alone'
    assert source.index(command[:40]) > source.index("already exists, skipping"), \
        'event update must apply to existing realms too'


source = (ROOT / 'scripts/cluster/11-2-keycloak-config.sh').read_text()
check(source)
for old, new in [
    ('eventsEnabled=true', 'eventsEnabled=false'),
    ('eventsExpiration=2592000', 'eventsExpiration=0'),
    ('"LOGIN","LOGIN_ERROR","LOGOUT"', '"LOGIN_ERROR","LOGOUT"'),
    ('\nkc_exec update "realms/${REALM}/events/config"',
     '\n  kc_exec update "realms/${REALM}/events/config"'),
]:
    assert old in source, f'mutation target missing: {old}'
    try:
        check(source.replace(old, new, 1))
    except AssertionError:
        continue
    raise AssertionError(f'undetected mutation: {old}')
# The portal service account needs view-events or the LOGIN lookup is refused (403)
# and the user list silently stays on the session fallback.
bindings = (ROOT / 'scripts/cluster/13-2-narwhal-portal-bindings.sh').read_text()
roles = re.findall(r'^\s*for role in ([a-z\- ]+); do$', bindings, re.M)
assert any('view-events' in line.split() for line in roles), \
    'portal admin service account must be granted realm-management/view-events'
print('PASS: R340 user events enabled, 30-day retention, existing realms; 4 mutations caught')
