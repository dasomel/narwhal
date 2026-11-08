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

Case: `keycloak-admin` is lost or out of sync and `temp-admin` is already deleted, so 11-1 cannot log in.
Follow the [Keycloak bootstrap-admin recovery](https://www.keycloak.org/server/bootstrap-admin-recovery):
all Keycloak nodes must be stopped, then `kc.sh bootstrap-admin user` creates a one-time admin from a
password environment variable (never on the command line). With the operator that means scaling the
`Keycloak` CR to zero, running the command in a one-off Pod against the same database, and scaling back.
Not run against a live cluster; rehearse the Pod on a throwaway Keycloak first.

```bash
# 1. Stop GitOps from scaling Keycloak back up, then stop Keycloak (brief SSO outage).
kubectl --context narwhal -n argocd get application narwhal-platform -o jsonpath='{.spec.syncPolicy.automated}'   # expect prune and selfHeal true
kubectl --context narwhal -n argocd patch application narwhal-platform --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
kubectl --context narwhal -n iam patch keycloak keycloak --type merge -p '{"spec":{"instances":0}}'
kubectl --context narwhal -n iam wait --for=delete pod/keycloak-0 --timeout=300s

# 2. Put a new one-time password into the Secret the Pod reads (value never printed or put in argv).
NEWPW=$(openssl rand -base64 18 | tr -d '=/+')
printf '%s' "$NEWPW" | kubectl --context narwhal -n iam create secret generic keycloak-recovery-admin \
  --from-file=password=/dev/stdin --dry-run=client -o yaml | kubectl --context narwhal -n iam apply -f -
unset NEWPW

# 3. One-off Pod: same image and database as Keycloak, password only through the environment.
IMG=$(kubectl --context narwhal -n iam get keycloak keycloak -o jsonpath='{.status.image}' 2>/dev/null)
[ -n "$IMG" ] || IMG=quay.io/keycloak/keycloak:26.5.7   # match KEYCLOAK_VERSION in scripts/cluster/11-keycloak.sh
kubectl --context narwhal -n iam apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: keycloak-bootstrap-recovery
  namespace: iam
spec:
  restartPolicy: Never
  containers:
    - name: kc
      image: ${IMG}
      command: ["/opt/keycloak/bin/kc.sh", "bootstrap-admin", "user", "--username", "temp-admin", "--password:env", "KC_NEW_PW", "--no-prompt"]
      env:
        - { name: KC_DB, value: postgres }
        - { name: KC_DB_URL, value: "jdbc:postgresql://narwhal-db-rw.database.svc.cluster.local:5432/keycloak?sslmode=disable" }
        - { name: KC_DB_USERNAME, valueFrom: { secretKeyRef: { name: keycloak-db-secret, key: username } } }
        - { name: KC_DB_PASSWORD, valueFrom: { secretKeyRef: { name: keycloak-db-secret, key: password } } }
        - { name: KC_NEW_PW, valueFrom: { secretKeyRef: { name: keycloak-recovery-admin, key: password } } }
EOF
kubectl --context narwhal -n iam wait --for=jsonpath='{.status.phase}'=Succeeded pod/keycloak-bootstrap-recovery --timeout=300s
kubectl --context narwhal -n iam logs keycloak-bootstrap-recovery | tail -3

# 4. Clean up, start Keycloak, restore GitOps.
kubectl --context narwhal -n iam delete pod keycloak-bootstrap-recovery
kubectl --context narwhal -n iam patch keycloak keycloak --type merge -p '{"spec":{"instances":1}}'
kubectl --context narwhal -n argocd patch application narwhal-platform --type merge -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
```

Then make 11-1 able to log in with the one-time admin: write its password into `keycloak-initial-admin`
(`password` key, `username` `temp-admin`), run 11-1 (it recreates `platform-admin` from `keycloak-admin`
and deletes `temp-admin` again), and finally `kubectl --context narwhal -n iam delete secret keycloak-recovery-admin`.
The restore command assumes the `prune` and `selfHeal` values shown by the first check.

Rollback to the temporary admin is not offered: `temp-admin` is by design not kept.
