<#import "template.ftl" as layout>
<@layout.registrationLayout; section>
    <#if section = "header">
        ${msg("logoutConfirmTitle")}
    <#elseif section = "form">
        <div id="kc-logout-confirm" class="nw-form">
            <p class="nw-note">${msg("logoutConfirmHeader")}</p>
            <form class="nw-form" action="${url.logoutConfirmAction}" method="POST">
                <input type="hidden" name="session_code" value="${logoutConfirm.code}"/>
                <div class="nw-actions">
                    <button class="nw-btn" name="confirmLogout" id="kc-logout" type="submit">${msg("doLogout")}</button>
                    <#if (!logoutConfirm.skipLink) && client?? && client.baseUrl?has_content>
                        <a class="nw-btn-ghost" href="${client.baseUrl}">${msg("backToApplication")}</a>
                    </#if>
                </div>
            </form>
        </div>
    </#if>
</@layout.registrationLayout>
