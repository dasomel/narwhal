#!/usr/bin/env python3
"""R343: bootstrap and GitOps must publish the same canonical Keycloak issuer."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def hostname(source):
    match = re.search(r'^  hostname:\n(.*?)(?=^  \S)', source, re.M | re.S)
    assert match, 'Keycloak hostname block missing'
    block = match.group(1)
    host = re.search(r'^    hostname: (.+)$', block, re.M)
    strict = re.search(r'^    strict: (.+)$', block, re.M)
    assert host and strict, 'hostname/strict missing'
    return (host[1].replace('${DOMAIN}', '<domain>').replace('{{ .Values.baseDomain }}', '<domain>'),
            strict[1])


def check(bootstrap, gitops):
    assert hostname(bootstrap) == hostname(gitops) == ('https://keycloak.<domain>', 'true'), \
        'bootstrap/GitOps hostname must agree on canonical https URL and strict: true'


def main():
    bootstrap = (ROOT / 'scripts/cluster/11-keycloak.sh').read_text()
    gitops = (ROOT / 'gitops/charts/narwhal-platform/templates/keycloak-cr.yaml').read_text()
    check(bootstrap, gitops)
    mutations = [('https://keycloak.${DOMAIN}', 'keycloak.${DOMAIN}'),
                 ('    strict: true', '    strict: false')]
    for old, new in mutations:
        assert old in bootstrap, old
        try:
            check(bootstrap.replace(old, new), gitops)
        except AssertionError:
            continue
        raise AssertionError(f'mutation survived: {old}')
    print('ok: bootstrap/GitOps canonical hostname + strict agree; 2 mutations caught')


if __name__ == '__main__':
    main()
