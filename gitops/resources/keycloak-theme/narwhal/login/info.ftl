<#import "template.ftl" as layout>
<#-- Themed info page. For the logout-success case Keycloak lands here with no
     redirect target and the stock page is a dead end — detect it (localized
     message comparison works for both en/ko) and auto-continue to the portal
     login after a short pause, deriving the portal host from this host so the
     template stays domain-agnostic. Other info pages (required actions, email
     verification, …) keep their links and are never auto-redirected. -->
<#assign isLogoutSuccess = message?? && message.summary?has_content
  && (message.summary == msg("successLogout")
      || message.summary?contains("로그아웃")
      || message.summary?lower_case?contains("logged out"))>
<@layout.registrationLayout displayMessage=false; section>
    <#if section = "header">
        <#if messageHeader??>${kcSanitize(messageHeader)?no_esc}<#elseif message??>${kcSanitize(message.summary)?no_esc}</#if>
    <#elseif section = "form">
        <div id="kc-info-message" class="nw-form">
            <#if message?? && message.summary?has_content && messageHeader??>
              <p class="nw-note">${kcSanitize(message.summary)?no_esc}<#if requiredActions??><#list requiredActions><b><#items as reqActionItem>${kcSanitize(msg("requiredAction.${reqActionItem}"))?no_esc}<#sep>, </#items></b></#list></#if></p>
            </#if>
            <div class="nw-actions">
              <#if pageRedirectUri?has_content>
                <a class="nw-btn" style="text-decoration:none;display:inline-flex;align-items:center;justify-content:center;" href="${pageRedirectUri}">${kcSanitize(msg("backToApplication"))?no_esc}</a>
              <#elseif actionUri?has_content>
                <a class="nw-btn" style="text-decoration:none;display:inline-flex;align-items:center;justify-content:center;" href="${actionUri}">${kcSanitize(msg("proceedWithAction"))?no_esc}</a>
              <#elseif (client.baseUrl)?has_content>
                <a class="nw-btn-ghost" href="${client.baseUrl}">${kcSanitize(msg("backToApplication"))?no_esc}</a>
              </#if>
              <#if isLogoutSuccess && !(actionUri?has_content)>
                <p class="nw-note-sub" id="nw-auto-note" aria-live="polite"></p>
                <script>
                  // Logged out with nowhere to go — continue to the portal login
                  // automatically (portal /login self-starts the Keycloak flow).
                  (function () {
                    var portal = "https://" + location.hostname.replace(/^[^.]+/, "portal") + "/login";
                    var note = document.getElementById("nw-auto-note");
                    if (note) note.textContent = "Redirecting to portal login…";
                    setTimeout(function () { location.replace(portal); }, 1500);
                  })();
                </script>
              </#if>
            </div>
        </div>
    </#if>
</@layout.registrationLayout>
