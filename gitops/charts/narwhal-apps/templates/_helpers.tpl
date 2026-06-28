{{/*
narwhal-apps.host: returns "<prefix>.<baseDomain>"
Usage: {{ include "narwhal-apps.host" (dict "prefix" "keycloak" "ctx" .) }}
*/}}
{{- define "narwhal-apps.host" -}}
{{- printf "%s.%s" .prefix .ctx.Values.baseDomain -}}
{{- end -}}
