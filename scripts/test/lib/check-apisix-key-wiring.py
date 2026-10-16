#!/usr/bin/env python3
"""R257: every APISIX admin-key consumer reads the apisix-admin-key Secret (run from repo root)."""
import os

s = open("scripts/cluster/08-1-networking.sh").read()
assert "credentials:\n      secretName: apisix-admin-key" in s, "bootstrap values lack admin.credentials"
assert "--set config.apisix.existingSecret=apisix-admin-key" in s, "IC must use existingSecret"
assert 'config.apisix.adminKey="' not in s, "IC admin key passed as a literal"
assert s.index("resolve_apisix_keys ||") < s.index('helm upgrade --install apisix "$(chart apisix)"'), \
    "keys must exist before the APISIX install"
assert "secretName: apisix-admin-key" in open("gitops/charts/narwhal-apps/templates/apisix.yaml").read()
assert "get secret apisix-admin-key" in open("scripts/cluster/13-2-narwhal-portal-bindings.sh").read()

r = "scripts/cluster/rotate-apisix-admin-key.sh"
t = open(r).read()
assert os.access(r, os.X_OK), "rotate script must be executable"
assert '"old admin key:${OLD_ADMIN}:401"' in t and '"new admin key:${NEW_ADMIN}:200"' in t
assert "existingSecret=apisix-admin-key" in t
order = [t.index(m) for m in ("Updating Secret", "Restarting APISIX", "ingress controller at the Secret", "Verifying")]
assert order == sorted(order), "rotation order must be Secret -> APISIX -> IC -> verify"

lib = open("scripts/common/lib.sh").read()
assert "grep -q 'NotFound'" not in lib and 'grep -q "NotFound"' not in lib, "unanchored NotFound match"
assert lib.count("Error from server (NotFound)") >= 3
