#!/usr/bin/env python3
"""R281: OpenBao/Velero UI direct-entry rules redirect an arg-less app root to /sso (D-ssoentry-1).

The template carries Helm directives, so the rule blocks are cut out as text, not parsed as YAML.
--mutation-verify proves each assertion fails when its guarantee is removed."""

import re
import sys
from pathlib import Path

ROUTES = Path('gitops/charts/narwhal-platform/templates/apisix-routes.yaml')
RULES = {
    'openbao-sso-entry': ['/', '/ui', '/ui/'],
    'velero-ui-sso-entry': ['/'],
}
HEADERS = ('Strict-Transport-Security', 'X-Content-Type-Options', 'X-Frame-Options')


def block(text, name):
    m = re.search(r'^    - name: %s\n(.*?)(?=^    - name: |^---)' % re.escape(name), text, re.S | re.M)
    assert m, '%s rule missing' % name
    return m.group(1)


def check(text):
    catch_all = {'openbao-sso-entry': 'openbao', 'velero-ui-sso-entry': 'velero-ui'}
    for name, paths in RULES.items():
        b = block(text, name)
        got = re.findall(r'^          - "([^"]+)"$', b.split('upstreams:')[0], re.M)[-len(paths):]
        assert got == paths, '%s paths %s != %s' % (name, got, paths)
        assert re.search(r'^      priority: (\d+)$', b, re.M) and int(re.search(r'priority: (\d+)', b).group(1)) > 0, \
            '%s needs priority above the catch-all' % name
        assert 'name: serverless-pre-function' in b and 'phase: rewrite' in b, '%s redirect plugin' % name
        assert re.search(r'if ngx\.var\.args == nil or ngx\.var\.args == "" then\s+return ngx\.redirect\("/sso", 302\)\s+end', b), \
            '%s must redirect to /sso only when args are empty' % name
        assert b.count('ngx.redirect') == 1, '%s has an unconditional redirect' % name
        assert 'name: response-rewrite' in b and all(h in b for h in HEADERS), '%s lost security headers' % name
        assert '- name: %s\n' % catch_all[name] in b.split('upstreams:')[1], '%s upstream' % name
    assert text.count('D-ssoentry-1') >= 2, 'D-ssoentry-1 decision comments missing'


MUTATIONS = {
    'args check dropped (openbao)': lambda t: t.replace(
        'if ngx.var.args == nil or ngx.var.args == "" then', 'if true then', 1),
    'args check dropped (velero)': lambda t: t[::-1].replace(
        'if ngx.var.args == nil or ngx.var.args == "" then'[::-1], 'if true then'[::-1], 1)[::-1],
    'redirect target changed': lambda t: t.replace('ngx.redirect("/sso", 302)', 'ngx.redirect("/ui/", 302)', 1),
    'openbao /ui/ path dropped': lambda t: t.replace('          - "/ui/"\n', '', 1),
    'entry rule removed (velero)': lambda t: t.replace('- name: velero-ui-sso-entry', '- name: velero-ui-x', 1),
    'headers dropped (openbao)': lambda t: t[:t.index('- name: openbao-sso-entry')] + t[
        t.index('- name: openbao-sso-entry'):].replace('X-Frame-Options: "SAMEORIGIN"', 'X-Foo: "1"', 1),
    'priority dropped': lambda t: t.replace('- name: openbao-sso-entry\n      priority: 10', '- name: openbao-sso-entry', 1),
}


def main():
    text = (Path(__file__).resolve().parents[3] / ROUTES).read_text()
    try:
        check(text)
    except AssertionError as e:
        print('FAIL: %s' % e, file=sys.stderr)
        return 1
    if '--mutation-verify' in sys.argv:
        for label, fn in MUTATIONS.items():
            mutated = fn(text)
            assert mutated != text, 'mutation %r did not change the file' % label
            try:
                check(mutated)
            except AssertionError:
                continue
            print('FAIL: mutation not rejected: %s' % label, file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
