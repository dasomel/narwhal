# Keycloak permanent master-realm admin (#573, D-KC-ADMIN-573)

Keycloak 26's operator creates a temporary master-realm admin (`temp-admin`, Secret
`iam/keycloak-initial-admin`); the console warns about it. `scripts/cluster/11-1-keycloak-admin.sh`
replaces it with the permanent user `platform-admin` (realm role `admin`, the same rights as
`temp-admin`), stored in `iam/keycloak-admin` (`username`, `password`). It deletes `temp-admin` only
after `platform-admin` logs in and calls the master-realm admin API. Consumers (`11-2`, `11-3`, `13-2`,
`scripts/test/*`) use `keycloak-admin` when it exists, else `keycloak-initial-admin`.

Fresh install: `scripts/up.sh` runs 11-1 as a critical Phase 2 step between 11 and 11-2. Nothing to do.

## Migrate an existing cluster

Context is always `narwhal`. The script is idempotent; a second run changes nothing.

```bash
# 1. Sanity: operator-created Secret and current admin exist; no keycloak-admin yet.
kubectl --context narwhal -n iam get secret keycloak-initial-admin keycloak-admin
kubectl --context narwhal -n iam get pod keycloak-0

# 2. Get the repo scripts onto master-1 (same way as other re-runs, see docs/common/gitops-push.md
#    for GitOps; scripts/cluster/* are copied by the Vagrant/provider sync). Then run on master-1:
vagrant ssh master-1 -c "sudo -u vagrant bash /home/vagrant/scripts/cluster/11-1-keycloak-admin.sh"

# 3. Verify (no values printed).
kubectl --context narwhal -n iam get secret keycloak-admin -o jsonpath='{.data.username}' | base64 -d; echo
kubectl --context narwhal -n iam exec keycloak-0 -c keycloak -- /opt/keycloak/bin/kcadm.sh \
  config credentials --server http://localhost:8080 --realm master \
  --user "$(kubectl --context narwhal -n iam get secret keycloak-admin -o jsonpath='{.data.username}' | base64 -d)" \
  --password "$(kubectl --context narwhal -n iam get secret keycloak-admin -o jsonpath='{.data.password}' | base64 -d)"
kubectl --context narwhal -n iam exec keycloak-0 -c keycloak -- /opt/keycloak/bin/kcadm.sh \
  get users -r master --fields username        # expect platform-admin only, no temp-admin

# 4. Re-run to prove idempotency, then the SSO suite.
vagrant ssh master-1 -c "sudo -u vagrant bash /home/vagrant/scripts/cluster/11-1-keycloak-admin.sh"
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh --section=scopes"
```

Then log in to `https://keycloak.<domain>/admin/master/console/` as `platform-admin`: the "temporary
admin" banner is gone.

Not demonstrated by the static suite: after a Keycloak pod restart the operator still injects
`KC_BOOTSTRAP_ADMIN_*`. Keycloak creates the bootstrap user only when the master realm has no admin, so
`temp-admin` must not come back. Check once with `kubectl --context narwhal -n iam delete pod keycloak-0`
(brief SSO outage, owner approval) and repeat the `get users` command above.

## Recovery

- `keycloak-admin` lost or password out of sync and `temp-admin` already deleted: add a one-time admin
  from the pod, then re-run 11-1 (it logs in with the new bootstrap user only if it is named in
  `keycloak-initial-admin`, so pick that username):
  `kubectl --context narwhal -n iam exec -it keycloak-0 -c keycloak -- /opt/keycloak/bin/kc.sh bootstrap-admin user --username temp-admin --password:env KC_NEW_PW`
  (set `KC_NEW_PW` and update `keycloak-initial-admin` `password` to the same value), then run 11-1.
- Rollback to the temporary admin is not offered: `temp-admin` is by design not kept.
