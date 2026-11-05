#!/usr/bin/env python3
"""R306: Keycloak login/admin theme stays unified with the Portal and delivered as one directory mount.

D-KC-THEME-TOKENS: the login CSS copies the Portal's token values (portal/src/app/globals.css), so a
Portal palette change that is not mirrored fails here instead of silently splitting the two products.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
THEME = ROOT / 'gitops/charts/narwhal-platform/keycloak-theme/narwhal'
PORTAL_CSS = ROOT / 'portal/src/app/globals.css'
LOGIN_CSS = THEME / 'login/resources/css/narwhal.css'


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
    for rel in ('login/resources/fonts/PretendardVariable-subset.woff2', 'login/resources/img/favicon.ico',
                'admin/resources/img/logo.svg', 'admin/resources/img/favicon.svg'):
        assert (THEME / rel).is_file() and (THEME / rel).stat().st_size > 100, f'missing theme asset {rel}'

    cr = (ROOT / 'gitops/charts/narwhal-platform/templates/keycloak-cr.yaml').read_text()
    assert 'mountPath: /opt/keycloak/themes/narwhal\n' in cr, 'theme must be one directory mount'
    # The CR lists every file literally (so the raw template stays valid YAML); it must match the tree exactly.
    listed = dict(re.findall(r'- key: "([^"]+)"\n\s+path: "([^"]+)"', cr))
    tree = {('narwhal__' + str(f.relative_to(THEME)).replace('/', '__')): str(f.relative_to(THEME))
            for f in THEME.rglob('*') if f.is_file()}
    assert listed == tree, f'keycloak-cr.yaml items differ from keycloak-theme/narwhal: only in CR {sorted(set(listed) - set(tree))}, only on disk {sorted(set(tree) - set(listed))}'
    assert 'subPath:' not in cr, 'subPath mounts do not follow ConfigMap updates'
    assert 'checksum/keycloak-theme:' in cr, 'Keycloak must roll when a theme file changes (themes are cached)'
    cfg = (ROOT / 'scripts/cluster/11-2-keycloak-config.sh').read_text()
    for realm in ('${REALM}', 'master'):
        assert re.search(r'update "realms/' + re.escape(realm) + r'" -s "loginTheme=narwhal" -s "adminTheme=narwhal"', cfg), \
            f'11-2 must set loginTheme and adminTheme on realm {realm}'
    print('PASS: R306 login CSS mirrors Portal tokens, themes extend keycloak.v2, one directory mount + checksum roll, 11-2 sets both realms')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print(f'FAIL: R306 {e}', file=sys.stderr)
        sys.exit(1)
