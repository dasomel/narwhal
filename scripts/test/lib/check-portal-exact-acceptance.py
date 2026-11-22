#!/usr/bin/env python3
"""R395: Exact, explicit, auditable acceptance and unused-rule API/UI seam."""
import copy
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    'data': 'portal/src/data/compliance-acceptance-rules.json',
    'loader': 'portal/src/lib/compliance-acceptance.ts',
    'matcher': 'portal/src/lib/compliance-accepted.ts',
    'comp': 'portal/src/lib/compliance.ts',
    'ui': 'portal/src/components/compliance/failure-counters.tsx',
    'i18n': 'portal/src/lib/i18n.ts',
    'reg': 'scripts/test/regression-check-kakao.sh',
}


def check(texts):
    errors = []
    data = json.loads(texts['data'])
    seen = set()
    targets = set()
    for domain in ('configAudit', 'rbac', 'infra'):
        for rule in data[domain]:
            if set(rule) - {'id', 'scope', 'namespace', 'kind', 'name', 'checks', 'class', 'reasonKey', 'owner', 'reviewedAt'}:
                errors.append('unknown selector or rule field')
            for field in ('id', 'scope', 'kind', 'name', 'reasonKey', 'owner', 'reviewedAt'):
                if not isinstance(rule.get(field), str) or not rule[field].strip():
                    errors.append(f'{domain}: missing {field}')
            if rule.get('id') in seen:
                errors.append('duplicate id')
            seen.add(rule.get('id'))
            ns = rule.get('namespace')
            if rule.get('scope') not in ('cluster', 'namespace') or not isinstance(ns, str):
                errors.append('invalid scope/namespace')
            elif (rule['scope'] == 'cluster') != (ns == ''):
                errors.append('scope/namespace mismatch')
            if domain == 'rbac' and (rule.get('kind'), rule.get('scope')) not in (
                    ('Role', 'namespace'), ('ClusterRole', 'cluster')):
                errors.append('RBAC kind/scope mismatch')
            if domain == 'configAudit' and rule.get('kind') not in (
                    'Pod', 'ReplicaSet', 'DaemonSet', 'StatefulSet', 'CronJob'):
                errors.append('invalid workload kind')
            for field in ('name', 'namespace'):
                value = rule.get(field, '')
                if value != value.strip() or re.search(r'[*?\[\]()^$|\\]', value):
                    errors.append('pattern target')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', rule.get('reviewedAt', '')):
                errors.append('missing review date')
            checks = rule.get('checks')
            if not isinstance(checks, list) or not checks:
                errors.append('checks must be explicit and nonempty')
            else:
                for cid in checks:
                    if not isinstance(cid, str) or not re.fullmatch(r'(KSV\d{3}|KCV\d{4})', cid):
                        errors.append('invalid check ID')
                    target = (domain, rule.get('scope'), ns, rule.get('kind'), rule.get('name'), cid)
                    if target in targets:
                        errors.append('overlapping target/check')
                    targets.add(target)
            reason = re.escape(rule.get('reasonKey', ''))
            if len(re.findall(r'"' + reason + r'": "[^"\n]+"', texts['i18n'])) != 2:
                errors.append('missing bilingual reason')
            paths = re.findall(r'(?:gitops|scripts|portal)/[\w./-]+', rule.get('owner', ''))
            if not paths or any(not (ROOT / path).is_file() for path in paths):
                errors.append('owner must identify existing repo pin/source')
    required = {
        'matcher': [
            'if (kind === "ReplicaSet" && deploymentOwners === undefined) return null',
            'configRuleIndex.get(`${scope}\\0${namespace}\\0${kind}\\0${normalizedName}`)',
            'rbacRuleIndex.get(`${scope}\\0${namespace}\\0${kind}\\0${roleName}`)',
        ],
        'loader': ['reason: common.reason', 'owner: raw.owner', 'reviewedAt: raw.reviewedAt'],
        'comp': ['unusedAcceptanceRules = findUnusedAcceptanceRules(',
                 'unusedAcceptanceRuleCount: unusedAcceptanceRules.length',
                 'for (const id of classified.matchedRuleIds) matchedRuleIds.add(id)',
                 'rbac: rbacRes.status === "fulfilled"'],
        'ui': ['summary.unusedAcceptanceRuleCount', 'summary.unusedAcceptanceRules.map',
               '{rule.id}', '{rule.kind}/{rule.name}', 'compliance.summary.unusedRulesHint'],
        'reg': ['check R395 ', 'check-portal-exact-acceptance.py --mutation-verify'],
    }
    for key, needles in required.items():
        for needle in needles:
            if needle not in texts[key]:
                errors.append(f'{key}: missing {needle}')
    for name in ('matchConfigAuditException', 'matchRbacException', 'matchInfraException'):
        body = texts['matcher'].split('export function '+name, 1)[1].split('\nexport ', 1)[0]
        pre_lookup = body.split('const rules =', 1)[0]
        pre_lookup = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/', '', pre_lookup)
        if re.search(r'\breturn\s+(?!null\b)', pre_lookup):
            errors.append(f'{name}: acceptance bypasses exact lookup')
        if 'rule.checks.has(normalizedCheck)' not in body:
            errors.append(f'{name}: no explicit check membership')
        if re.search(r'\.(?:includes|startsWith|endsWith|test|exec|match)\(', body):
            errors.append(f'{name}: pattern acceptance')
    for key in ('comp', 'matcher'):
        # D-P2-1: Reject blanket paths even when a new exact table also exists.
        code = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/', '', texts[key])
        if re.search(r'\b(?:SYSTEM_NAMESPACES|UPSTREAM_RBAC_ROLE_PATTERNS|isBuiltInRbacRole|isSystemNamespace)\b', code):
            errors.append('blanket acceptance')
    return errors


def main():
    texts = {k: (ROOT / p).read_text() for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    mutations = []
    for field in ('id', 'scope', 'namespace', 'kind', 'name', 'checks', 'reasonKey', 'owner', 'reviewedAt'):
        data = json.loads(texts['data'])
        del data['configAudit'][0][field]
        mutations.append((f'missing {field}', 'data', json.dumps(data)))
    for field, value in (('name', 'cilium*'), ('namespace', '*'), ('checks', ['*'])):
        data = json.loads(texts['data'])
        data['configAudit'][0][field] = value
        mutations.append((f'wildcard {field}', 'data', json.dumps(data)))
    for selector in ('namePrefix', 'labels', 'nameRegex'):
        data = json.loads(texts['data'])
        data['rbac'][0][selector] = 'system:'
        mutations.append((f'forbidden {selector}', 'data', json.dumps(data)))
    replacements = [
        ('prefix acceptance', 'matcher', 'rule.checks.has(normalizedCheck)', 'roleName.startsWith("system:")'),
        ('regex acceptance', 'matcher', 'rule.checks.has(normalizedCheck)', '/system:/.test(roleName)'),
        ('label-only acceptance', 'matcher', 'if (kind !== "Role" && kind !== "ClusterRole") return null',
         'if (labels["kubernetes.io/bootstrapping"]) return fakeMatch;\n  if (kind !== "Role" && kind !== "ClusterRole") return null'),
        ('drop namespace', 'matcher', '${scope}\\0${namespace}\\0${kind}\\0${normalizedName}', '${scope}\\0${kind}\\0${normalizedName}'),
        ('drop kind', 'matcher', '${scope}\\0${namespace}\\0${kind}\\0${roleName}', '${scope}\\0${namespace}\\0${roleName}'),
        ('all checks', 'matcher', 'rule.checks.has(normalizedCheck)', 'true'),
        ('substring checks', 'matcher', 'rule.checks.has(normalizedCheck)', 'rule.name.includes(normalizedCheck)'),
        ('unowned RS', 'matcher', 'if (kind === "ReplicaSet" && deploymentOwners === undefined) return null', ''),
        ('summary count', 'comp', 'unusedAcceptanceRuleCount: unusedAcceptanceRules.length', ''),
        ('UI list', 'ui', 'summary.unusedAcceptanceRules.map', 'other.map'),
        ('registration', 'reg', 'check R395 ', 'check R999 '),
    ]
    for name, key, old, new in replacements:
        assert old in texts[key], name
        mutations.append((name, key, texts[key].replace(old, new, 1)))
    mutations.append(('namespace blanket', 'comp', texts['comp'] + '\nconst SYSTEM_NAMESPACES = ["kube-system"]'))
    if '--mutation-verify' in sys.argv:
        for name, key, value in mutations:
            mutated = copy.copy(texts)
            mutated[key] = value
            try:
                caught = bool(check(mutated))
            except (KeyError, TypeError):
                caught = True
            assert caught, f'mutation survived: {name}'
        print(f'PASS: R395 exact acceptance ({len(mutations)} mutations rejected)')
    else:
        print('PASS: R395 exact acceptance')
    return 0


if __name__ == '__main__':
    sys.exit(main())
