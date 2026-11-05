#!/usr/bin/env python3
"""R306: Keycloak login/admin theme stays unified with the Portal and delivered as one directory mount.

D-KC-THEME-TOKENS: the login CSS copies the Portal's token values (portal/src/app/globals.css), so a
Portal palette change that is not mirrored fails here instead of silently splitting the two products.
"""

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
THEME = ROOT / 'gitops/charts/narwhal-platform/keycloak-theme/narwhal'
PORTAL_CSS = ROOT / 'portal/src/app/globals.css'
LOGIN_CSS = THEME / 'login/resources/css/narwhal.css'


def render():
    # D1: derive Helm values from the app-of-apps source to catch projection errors before a push.
    # Cost: two local Helm renders. Escape hatch: --rendered accepts a render using live Argo values.
    source = yaml.safe_load((ROOT / 'gitops/apps/app-of-apps.yaml').read_text())['spec']['source']
    apps = subprocess.check_output(
        ['helm', 'template', 'narwhal-apps', str(ROOT / 'gitops/charts/narwhal-apps'),
         '--show-only', 'templates/narwhal-platform.yaml', '-f', '-'],
        input=yaml.safe_dump(source['helm']['valuesObject']), text=True)
    values = yaml.safe_load(apps)['spec']['source']['helm']['valuesObject']
    out = subprocess.check_output(
        ['helm', 'template', 'narwhal-platform', str(THEME.parents[1]), '-n', 'devtools', '-f', '-'],
        input=yaml.safe_dump(values), text=True)
    return list(yaml.safe_load_all(out))


def check_projection(docs):
    cms = {(d['metadata']['namespace'], d['metadata']['name']): d
           for d in docs if d and d.get('kind') == 'ConfigMap'}
    crs = [d for d in docs if d and d.get('kind') == 'Keycloak']
    assert len(crs) == 1, 'expected one rendered Keycloak CR'
    cr = crs[0]
    refs = []
    for volume in cr['spec']['unsupported']['podTemplate']['spec']['volumes']:
        if 'configMap' in volume:
            refs.append(volume['configMap'])
        refs.extend(s['configMap'] for s in volume.get('projected', {}).get('sources', []) if 'configMap' in s)
    seen = set()
    for ref in refs:
        name = ref['name']
        cm = cms.get((cr['metadata']['namespace'], name))
        assert cm is not None, f'rendered ConfigMap {name} missing'
        keys = set(cm.get('data', {})) | set(cm.get('binaryData', {}))
        items = ref.get('items', [])
        listed = [item['key'] for item in items]
        missing = set(listed) - keys
        assert not missing, f'rendered ConfigMap {name} missing referenced keys: {sorted(missing)}'
        if name.startswith('keycloak-theme-narwhal'):
            assert len(listed) == len(set(listed)), f'duplicate items in {name}'
            assert set(listed) == keys, f'rendered ConfigMap {name} has unmounted keys: {sorted(keys - set(listed))}'
            # Conservative wire-size bound also leaves headroom below Kubernetes' decoded data limit.
            size = len(json.dumps(cm).encode())
            assert size < 1024 * 1024, f'{name} exceeds 1 MiB: {size} bytes'
            seen.add(name)
    assert seen == {'keycloak-theme-narwhal', 'keycloak-theme-narwhal-assets'}, 'both theme ConfigMaps must be projected'


def mutation_verify(docs):
    count = 0
    for original in docs:
        if not original or original.get('kind') != 'ConfigMap' or not original['metadata']['name'].startswith('keycloak-theme-narwhal'):
            continue
        mutated = copy.deepcopy(docs)
        cm = next(d for d in mutated if d and d.get('kind') == 'ConfigMap' and d['metadata'] == original['metadata'])
        field = 'data' if cm.get('data') else 'binaryData'
        key = next(iter(cm[field]))
        del cm[field][key]
        try:
            check_projection(mutated)
        except AssertionError as e:
            assert 'missing referenced keys' in str(e), f'mutation failed for the wrong reason: {e}'
            count += 1
        else:
            raise AssertionError(f'removed rendered key {key} was not detected')
    assert count == 2, 'must mutate both text and binary ConfigMaps'
    print(f'PASS: R306 removed-key mutations caught: {count}')


def block(text, opener):
    """Body of the first `<opener> {` rule (no nested braces in these blocks)."""
    m = re.search(re.escape(opener) + r'\s*\{(.*?)\n\}', text, re.S)
    assert m, f'rule {opener!r} not found'
    return m.group(1)


def decls(body):
    return {k: re.sub(r'\s+', ' ', v.strip()) for k, v in re.findall(r'(--[\w-]+):\s*([^;]+);', body)}


def main():
    portal, login = PORTAL_CSS.read_text(), LOGIN_CSS.read_text()
    p_light, p_dark = decls(block(portal, ':root')), decls(block(portal, '.dark'))
    t_light, t_dark = decls(block(login, ':root')), decls(block(login, 'html.nw-dark'))
    # Every token the theme mirrors must equal the Portal value; the theme mirrors these names.
    mirrored = ['--narwhal-accent', '--narwhal-accent-soft', '--narwhal-wave', '--narwhal-body', '--narwhal-belly',
                '--narwhal-detail', '--narwhal-horn', '--narwhal-success', '--narwhal-warning', '--narwhal-danger',
                '--narwhal-text-mono', '--background', '--foreground', '--card', '--card-foreground', '--primary',
                '--primary-foreground', '--secondary', '--muted', '--accent', '--destructive', '--border', '--input',
                '--ring', '--radius']
    for name in mirrored:
        assert t_light.get(name) == p_light.get(name), f'light {name}: theme {t_light.get(name)} != portal {p_light.get(name)}'
        if name in p_dark:
            assert t_dark.get(name) == p_dark[name], f'dark {name}: theme {t_dark.get(name)} != portal {p_dark[name]}'
        else:
            assert name not in t_dark, f'dark {name} is not overridden by the Portal; the theme must not override it either'
    for name in ('--muted-foreground',):
        assert t_light[name] == p_light[name], f'light {name}: theme {t_light[name]} != portal {p_light[name]}'
        assert t_dark[name] == p_dark[name], f'dark {name}: theme {t_dark[name]} != portal {p_dark[name]}'

    login_props = (THEME / 'login/theme.properties').read_text()
    assert re.search(r'^parent=keycloak\.v2$', login_props, re.M), 'login theme must extend keycloak.v2'
    assert re.search(r'^darkMode=false$', login_props, re.M), 'cookie sync owns dark mode (darkMode=false)'
    assert 'narwhal-theme' in (THEME / 'login/resources/js/narwhal.js').read_text()
    admin_props = (THEME / 'admin/theme.properties').read_text()
    for key in ('parent=keycloak.v2', 'logo=/img/logo.svg', 'favIcon=/img/favicon.svg', 'styles=css/narwhal-admin.css'):
        assert re.search('^' + re.escape(key) + '$', admin_props, re.M), f'admin theme.properties lacks {key}'
    for rel in ('login/resources/fonts/Pretendard-Regular.subset.woff2', 'login/resources/fonts/Pretendard-Bold.subset.woff2',
                'login/resources/img/favicon.ico',
                'admin/resources/img/logo.svg', 'admin/resources/img/favicon.svg'):
        assert (THEME / rel).is_file() and (THEME / rel).stat().st_size > 100, f'missing theme asset {rel}'

    # SIL OFL 1.1 reserves the name 'Pretendard': a re-subset is a Modified Version, so
    # the shipped faces must be the upstream files, with the license text beside them.
    fonts = THEME / 'login/resources/fonts'
    ofl = fonts / 'OFL.txt'
    assert ofl.is_file(), 'OFL.txt must ship beside the font (login/resources/fonts/OFL.txt)'
    ofl_text = ofl.read_text()
    assert 'SIL OPEN FONT LICENSE Version 1.1' in ofl_text and 'Kil Hyung-jin' in ofl_text, 'OFL.txt must be the OFL 1.1 text with the Pretendard copyright'
    upstream = {  # pretendard@1.3.9 dist/web/static/woff2-subset, sha256 of the published files
        'Pretendard-Regular.subset.woff2': '01dd73155fdfab7ce9b25224523e85a96927e21aef97f21957d41f1bfa7e3878',
        'Pretendard-Bold.subset.woff2': '78eb71c33101ee7d4f8d1b777d193a12d00f8a296e712a8c417cf27abe946397',
    }
    shipped = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in fonts.glob('*.woff2')}
    assert shipped == upstream, f'shipped fonts must be the unmodified upstream Pretendard subsets (OFL Reserved Font Name): {shipped}'
    portal_fonts = ROOT / 'portal/src/app/fonts'
    assert (portal_fonts / 'OFL.txt').read_bytes() == ofl.read_bytes(), 'Portal must ship the same OFL text'
    portal_sha = '9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4'
    assert hashlib.sha256((portal_fonts / 'PretendardVariable.woff2').read_bytes()).hexdigest() == portal_sha, 'Portal font differs from pretendard@1.3.9 upstream'
    assert portal_sha in (ROOT / 'portal/NOTICE').read_text(), 'Portal NOTICE must record font provenance'
    assert 'COPY src/app/fonts/OFL.txt ./OFL-Pretendard.txt' in (ROOT / 'portal/Dockerfile').read_text(), 'Portal image must include OFL'
    css = (THEME / 'login/resources/css/narwhal.css').read_text()
    for face in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css):
        assert (fonts / face).is_file(), f'narwhal.css references missing font {face}'

    cr = (ROOT / 'gitops/charts/narwhal-platform/templates/keycloak-cr.yaml').read_text()
    assert 'mountPath: /opt/keycloak/themes/narwhal\n' in cr, 'theme must be one directory mount'
    # The CR lists every file literally (so the raw template stays valid YAML); it must match the tree exactly.
    listed = dict(re.findall(r'- key: "([^"]+)"\n\s+path: "([^"]+)"', cr))
    tree = {('narwhal__' + str(f.relative_to(THEME)).replace('/', '__')): str(f.relative_to(THEME))
            for f in THEME.rglob('*') if f.is_file()}
    assert listed == tree, f'keycloak-cr.yaml items differ from keycloak-theme/narwhal: only in CR {sorted(set(listed) - set(tree))}, only on disk {sorted(set(tree) - set(listed))}'
    assert 'subPath:' not in cr, 'subPath mounts do not follow ConfigMap updates'
    assert 'checksum/keycloak-theme:' in cr, 'Keycloak must roll when a theme file changes (themes are cached)'
    parser = argparse.ArgumentParser()
    parser.add_argument('--rendered', type=Path, help='Helm output rendered with the live ArgoCD values')
    parser.add_argument('--mutation-verify', action='store_true')
    args = parser.parse_args()
    docs = list(yaml.safe_load_all(args.rendered.read_text())) if args.rendered else render()
    check_projection(docs)
    if args.mutation_verify:
        mutation_verify(docs)
    cfg = (ROOT / 'scripts/cluster/11-2-keycloak-config.sh').read_text()
    for realm in ('${REALM}', 'master'):
        assert re.search(r'update "realms/' + re.escape(realm) + r'" -s "loginTheme=narwhal" -s "adminTheme=narwhal"', cfg), \
            f'11-2 must set loginTheme and adminTheme on realm {realm}'
    print('PASS: R306 Portal tokens, upstream fonts + OFL, rendered ConfigMap keys + budget, directory mount + checksum, both realms')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print(f'FAIL: R306 {e}', file=sys.stderr)
        sys.exit(1)
