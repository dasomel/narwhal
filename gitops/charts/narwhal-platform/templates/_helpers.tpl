{{/*
narwhal-platform.host: returns "<prefix>.<baseDomain>"
Usage: {{ include "narwhal-platform.host" (dict "prefix" "keycloak" "ctx" .) }}
*/}}
{{- define "narwhal-platform.host" -}}
{{- printf "%s.%s" .prefix .ctx.Values.baseDomain -}}
{{- end -}}
