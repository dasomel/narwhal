{{/*
narwhal-platform.host: returns "<prefix>.<baseDomain>"
Usage: {{ include "narwhal-platform.host" (dict "prefix" "keycloak" "ctx" .) }}
*/}}
{{- define "narwhal-platform.host" -}}
{{- printf "%s.%s" .prefix .ctx.Values.baseDomain -}}
{{- end -}}

{{/*
narwhal-platform.gatewayAdminOnly: serverless plugin pair that restricts an
openid-connect route to Keycloak group cluster-admin (D-GATEWAY-ADMIN-GROUP).
Usage (route plugins list, after openid-connect):
  {{- include "narwhal-platform.gatewayAdminOnly" . | nindent 8 }}
Why two plugins: openid-connect (priority 2599) overwrites X-Userinfo with the
base64 JSON of the id-token claims of the verified session (set_userinfo_header);
serverless-pre-function (priority 10000) runs BEFORE it and strips any
client-supplied X-Userinfo, serverless-post-function (priority -2000) runs AFTER it
and reads only what the plugin set. An unauthenticated request never reaches the
post function (openid-connect redirects to Keycloak first). Fail closed: a missing,
undecodable or group-less header is 403. Cost: relies on the route keeping
set_userinfo_header: true and the `groups` claim (full.path=false) in the id token.
Escape hatch: delete this include from the route.
*/}}
{{- define "narwhal-platform.gatewayAdminOnly" -}}
- name: serverless-pre-function
  enable: true
  config:
    phase: rewrite
    functions:
      - |
        return function(conf, ctx)
          ngx.req.clear_header("X-Userinfo")
        end
- name: serverless-post-function
  enable: true
  config:
    phase: rewrite
    functions:
      - |
        return function(conf, ctx)
          local core = require("apisix.core")
          local ok = false
          local raw = ngx.req.get_headers()["X-Userinfo"]
          if type(raw) == "string" then
            local dec = ngx.decode_base64(raw)
            local u = dec and core.json.decode(dec)
            if type(u) == "table" and type(u.groups) == "table" then
              for _, g in ipairs(u.groups) do
                if g == "cluster-admin" then ok = true end
              end
            end
          end
          if not ok then
            ngx.status = 403
            ngx.header["Content-Type"] = "text/plain; charset=utf-8"
            ngx.say("403 Forbidden: Keycloak group cluster-admin required")
            return ngx.exit(403)
          end
        end
{{- end -}}
