#!/usr/bin/env python3
"""R339: Figma login split, single localized copy, accessible realm and compact wordmark."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
THEME = ROOT / 'gitops/charts/narwhal-platform/keycloak-theme/narwhal/login'
CSS = THEME / 'resources/css/narwhal.css'
JS = THEME / 'resources/js/narwhal.js'
MESSAGES = {lang: (THEME / f'messages/messages_{lang}.properties').read_text()
            for lang in ('en', 'ko')}
SPLIT = 'html.login-pf .pf-v5-c-login__container:has(#kc-form-login)'


def rule(css, selector):
    match = re.search(re.escape(selector) + r'\s*\{([^{}]*)\}', css)
    assert match, f'missing rule: {selector}'
    return match[1]


def assert_contract(css, js, messages):
    assert 'https://www.figma.com/design/Zg2T4x6slnstMJrkcEswah' in css
    assert re.search(r'^parent=keycloak\.v2$', (THEME / 'theme.properties').read_text(), re.M)
    assert not (THEME / 'login.ftl').exists() and not (THEME / 'template.ftl').exists(), 'login template override is out of scope'
    assert 'document.documentElement.lang' in js
    assert 'textContent = node.getAttribute("data-" + language)' in js
    for key, node, en, ko in (
        ('nwBrandHeadline', 'headline', r'One portal for\nthe whole platform', r'하나의 포털에서\n플랫폼 전체를 다룹니다'),
        ('nwBrandTagline', 'tagline', 'Catalog, deploys, observability and policy behind one SSO.', '서비스 카탈로그, 배포, 관측, 보안 정책을 SSO 한 번으로.'),
    ):
        assert f'{key}={en}' in messages['en'] and f'{key}={ko}' in messages['ko'], f'{key} bundle mismatch'
        for lang, value in (('en', en), ('ko', ko)):
            # Exact JS source spelling: one escape becomes a newline at runtime, two stay literal.
            assert f'{node}.setAttribute("data-{lang}", "{value}");' in js, f'{node} {lang} missing or over-escaped'
        assert f'copy.appendChild({node})' in js, f'{node} detached'
    assert js.count('document.createElement("p")') == 2
    assert 'document.createElement("h1")' not in js, 'Keycloak already owns the h1'
    assert 'header.appendChild' not in js, 'brand copy must not be duplicated in realm header'
    assert 'querySelector(".pf-v5-c-login__container:has(#kc-form-login)")' in js, 'brand must be login-only'
    assert 'if (login && header)' in js
    assert 'white-space: pre-line' in rule(css, 'html.login-pf .nw-login-brand .nw-login-copy-headline')
    layout = rule(css, SPLIT)
    assert 'grid-template-columns: clamp(22.5rem, 44vw, 40rem) minmax(0, 1fr)' in layout
    assert 'padding: 3rem 0' in layout and 'column-gap: 0' in layout, 'right panel must have symmetric space'
    main = rule(css, SPLIT + ' .pf-v5-c-login__main')
    assert 'justify-self: center' in main and 'width: min(100%, 25rem)' in main
    assert 'box-shadow: none' in main and 'border-radius: 0' in main
    realm = rule(css, SPLIT + ' #kc-header')
    assert 'clip-path: inset(50%)' in realm and 'display: none' not in realm, 'realm must remain accessible'
    title = rule(css, SPLIT + ' .pf-v5-c-login__main-header .pf-v5-c-title')
    for declaration in ('font-size: 1.75rem', 'font-weight: 700', 'text-align: left'):
        assert declaration in title, f'title: {declaration}'
    assert '@media (max-width: 900px)' in css
    mobile = css.split('@media (max-width: 900px)', 1)[1]
    assert 'display: none' in rule(mobile, SPLIT + ' .nw-login-brand')
    assert 'padding: 1.5rem' in rule(mobile, SPLIT)
    assert 'clip-path: none' in rule(mobile, SPLIT + ' #kc-header')
    wordmark = rule(mobile, SPLIT + ' #kc-header-wrapper')
    assert 'flex-direction: row' in wordmark and 'font-size: 1.125rem' in wordmark
    assert 'flex: 0 0 2.75rem' in rule(mobile, SPLIT + ' #kc-header-wrapper::before')
    assert 'width: 100%' in rule(mobile, SPLIT + ' .pf-v5-c-login__main-footer')
    assert '.pf-v5-c-login__container:not(:has(#kc-form-login))' in css
    assert 'prefers-reduced-motion: reduce' in css and 'animation: none' in css
    assert 'html.nw-dark' in css


def mutation_verify(css, js):
    changes = [
        ('breakpoint', css.replace('@media (max-width: 900px)', '@media (max-width: 901px)', 1), js),
        ('unsafe copy', css, js.replace('textContent = node.getAttribute', 'innerHTML = node.getAttribute', 1)),
        ('literal newline', css, js.replace(r'One portal for\n', r'One portal for\\n', 1)),
        ('missing brand copy', css, js.replace('headline.setAttribute("data-en"', 'missing.setAttribute("data-en"', 1)),
        ('duplicate h1', css, js.replace('document.createElement("p")', 'document.createElement("h1")', 1)),
        ('duplicate header copy', css, js.replace('copy.appendChild(headline)', 'header.appendChild(headline)', 1)),
        ('non-login leak', css, js.replace('.pf-v5-c-login__container:has(#kc-form-login)', '.pf-v5-c-login__container', 1)),
        ('asymmetric panel', css.replace('padding: 3rem 0', 'padding: 3rem 4rem', 1), js),
        ('realm visible', css.replace('clip-path: inset(50%)', 'clip-path: none', 1), js),
        ('title size', css.replace('font-size: 1.75rem', 'font-size: 1.125rem', 1), js),
        ('oversized mascot', css.replace('flex: 0 0 2.75rem', 'flex: 0 0 6.5rem', 1), js),
        ('lost mobile gutter', css.replace('    padding: 1.5rem;', '    padding: 0;', 1), js),
        ('lost line break', css.replace('white-space: pre-line', 'white-space: normal', 1), js),
    ]
    for name, mut_css, mut_js in changes:
        assert (mut_css, mut_js) != (css, js), f'{name} mutation did not apply'
        try:
            assert_contract(mut_css, mut_js, MESSAGES)
        except AssertionError:
            print(f'PASS: R339 mutation rejected: {name}')
        else:
            raise AssertionError(f'{name} mutation escaped')


def main():
    css, js = CSS.read_text(), JS.read_text()
    assert_contract(css, js, MESSAGES)
    mutation_verify(css, js)
    print('PASS: R339 single localized brand, split centering, accessible realm, 28px title, 44px compact wordmark, 24px gutters; 13 mutations rejected')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as error:
        print(f'FAIL: R339 {error}', file=sys.stderr)
        sys.exit(1)
