<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password'); section>
    <#if section = "header">
        ${msg("loginAccountTitle")}
    <#elseif section = "form">
        <#if realm.password>
        <form id="kc-form-login" class="nw-form" action="${url.loginAction}" method="post">
            <div class="nw-field">
                <label class="nw-label" for="username"><#if !realm.loginWithEmailAllowed>${msg("username")}<#elseif !realm.registrationEmailAsUsername>${msg("usernameOrEmail")}<#else>${msg("email")}</#if></label>
                <input id="username" class="nw-input" name="username" type="text" value="${(login.username!'')}" autofocus autocomplete="username"
                       aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>"/>
            </div>
            <div class="nw-field">
                <label class="nw-label" for="password">${msg("password")}</label>
                <input id="password" class="nw-input" name="password" type="password" autocomplete="current-password"
                       aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>"/>
            </div>
            <#if messagesPerField.existsError('username','password')>
                <div class="nw-err" aria-live="polite">${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</div>
            </#if>
            <#if (realm.rememberMe && !usernameHidden??) || realm.resetPasswordAllowed>
            <div class="nw-row">
                <#if realm.rememberMe && !usernameHidden??>
                    <label class="nw-check"><input name="rememberMe" type="checkbox" <#if login.rememberMe??>checked</#if>/> ${msg("rememberMe")}</label>
                <#else><span></span></#if>
                <#if realm.resetPasswordAllowed>
                    <a class="nw-link" href="${url.loginResetCredentialsUrl}">${msg("doForgotPassword")}</a>
                </#if>
            </div>
            </#if>
            <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if>/>
            <button class="nw-btn" name="login" id="kc-login" type="submit">${msg("doLogIn")}</button>
        </form>
        <script>
          // Explicit Enter handling. Implicit form submission exists, but the
          // first Enter is routinely swallowed by password-manager overlays and
          // by IME composition (isComposing / legacy keyCode 229 on Korean
          // input) — so wire it up deterministically: Enter on username moves
          // to password; Enter on password clicks the submit button (a real
          // click, so the button's name=login pair is included like a tap).
          (function () {
            var u = document.getElementById("username");
            var p = document.getElementById("password");
            var b = document.getElementById("kc-login");
            if (!u || !p || !b) return;
            function onEnter(e, act) {
              if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
              e.preventDefault();
              act();
            }
            u.addEventListener("keydown", function (e) { onEnter(e, function () { p.focus(); }); });
            p.addEventListener("keydown", function (e) { onEnter(e, function () { b.click(); }); });
          })();
        </script>
        </#if>
    <#elseif section = "info">
        <#if realm.password && realm.registrationAllowed && !registrationDisabled??>
            <span>${msg("noAccount")} <a class="nw-link" href="${url.registrationUrl}">${msg("doRegister")}</a></span>
        </#if>
    </#if>
</@layout.registrationLayout>
