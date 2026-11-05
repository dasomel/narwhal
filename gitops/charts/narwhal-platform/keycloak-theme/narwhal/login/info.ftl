<#import "template.ftl" as layout>
<#-- Info page, same structure as keycloak.v2's. The one addition: for the logout-success case
     Keycloak lands here with no redirect target and the stock page is a dead end. Detect it
     (localized message comparison works for en and ko) and continue to the Portal sign-in after a
     short pause, deriving the Portal host from this host so the template stays domain-agnostic.
     Other info pages (required actions, email verification, ...) keep their links and are never
     auto-redirected. -->
<#assign isLogoutSuccess = message?? && message.summary?has_content
  && message.summary == msg("successLogout")>
<@layout.registrationLayout displayMessage=false; section>
    <#if section = "header">
        <#if messageHeader??>
            ${kcSanitize(msg("${messageHeader}"))?no_esc}
        <#else>
            ${message.summary}
        </#if>
    <#elseif section = "form">
        <div id="kc-info-message">
            <p class="instruction">${message.summary}<#if requiredActions??><#list requiredActions>: <b><#items as reqActionItem>${kcSanitize(msg("requiredAction.${reqActionItem}"))?no_esc}<#sep>, </#items></b></#list></#if></p>
            <#if skipLink??>
            <#else>
                <#if pageRedirectUri?has_content>
                    <p><a href="${pageRedirectUri}" class="${properties.kcButtonPrimaryClass} ${properties.kcButtonBlockClass}">${kcSanitize(msg("backToApplication"))?no_esc}</a></p>
                <#elseif actionUri?has_content>
                    <p><a href="${actionUri}" class="${properties.kcButtonPrimaryClass} ${properties.kcButtonBlockClass}">${kcSanitize(msg("proceedWithAction"))?no_esc}</a></p>
                <#elseif (client.baseUrl)?has_content>
                    <p><a href="${client.baseUrl}" class="${properties.kcButtonSecondaryClass} ${properties.kcButtonBlockClass}">${kcSanitize(msg("backToApplication"))?no_esc}</a></p>
                </#if>
            </#if>
            <#if isLogoutSuccess && !(actionUri?has_content)>
                <p class="nw-note" id="nw-auto-note" role="status" aria-live="polite"></p>
                <script>
                  // Logged out with nowhere to go: continue to the Portal sign-in automatically
                  // (Portal /login self-starts the Keycloak flow).
                  (function () {
                    var portal = "https://" + location.hostname.replace(/^[^.]+/, "portal") + "/login";
                    var note = document.getElementById("nw-auto-note");
                    if (note) note.textContent = "${msg("nwRedirectingToPortal")?js_string}";
                    setTimeout(function () { location.replace(portal); }, 1500);
                  })();
                </script>
            </#if>
        </div>
    </#if>
</@layout.registrationLayout>
