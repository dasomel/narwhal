# Alerts and notification channels (narwhal#610)

`/alerts` lists firing alerts, including silenced/inhibited alerts, and refreshes every
30 seconds. Severity filtering is local. An unreachable/malformed Alertmanager
response is degraded, never evidence that there are no alerts. Navigation, the page
and `/api/alerts/firing` require the Portal `cluster-admin` identity. In the cluster
RBAC source, `oidc:cluster-admin` binds the Kubernetes `platform-admin` ClusterRole;
these are names for the same existing identity boundary, not two Portal roles.

Settings → Notification channels is cluster-admin only. Both GET and PUT
`/api/settings/notification-channels`, and POST `/api/alerts/test`, enforce that
role on the server. GET/PUT return channel enabled flags and per-key booleans only.
Every input is a replacement: omitted fields keep the stored value, null clears it.
The UI never receives stored values, including non-password fields. TLS defaults
to required unless `smtp_require_tls` is explicitly `false`. SMTP authentication
requires both username and password; unauthenticated SMTP is supported.

GitOps owns creation of Secret and AlertmanagerConfig
`monitoring/narwhal-notification-channels`. Portal gets both before saving,
updates the Secret with its resourceVersion, then patches the entire config spec
with a resourceVersion test. It never creates/deletes resources, lists Secrets,
or edits cluster RBAC. Disabled channels have no receiver/child route. Credentials
stay in the Secret; the config refers to its webhook/password keys.

Kubernetes cannot atomically save both resources. A rejected/lost write reports
an unknown/possibly partial outcome without retry or credential rollback. Reload
and verify before saving again. ResourceVersion preconditions detect competing
writers. Missing resources/permissions are degraded; provisioning remains with
the GitOps lane. No settings or credentials enter the Valkey cache.

`ALERTMANAGER_URL` follows the existing runtime config convention. Production
must configure it as `http://prometheus-stack-kube-prom-alertmanager.monitoring.svc:9093`.
The dedicated firing/test client also uses that address as its development default.
The GitOps lane owns service access, fixed-resource RBAC and Alertmanager selectors.

A test posts `NarwhalNotificationTest`, severity `info`, expiring after five minutes.
Acceptance confirms only Alertmanager ingestion, not reconciliation or channel
delivery. Wait for reconciliation and verify the recipient yourself. Existing
silences/inhibition and routing/group timing can affect delivery.

Unit tests cover validation, all toggle combinations, redaction, persistence errors
and role gating. `e2e/notifications.spec.ts` exercises real local browser/session/API
paths against disposable upstream fixtures. Neither substitutes for live operator
reconciliation, network policy, real SSO, Slack/Mattermost or SMTP delivery checks.
