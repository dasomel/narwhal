<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html lang="${locale.currentLanguageTag}">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="robots" content="noindex, nofollow"/>
  <title>Narwhal IDP</title>
  <#if properties.styles?has_content>
    <#list properties.styles?split(' ') as style>
      <link href="${url.resourcesPath}/${style}" rel="stylesheet"/>
    </#list>
  </#if>
</head>
<body class="nw-body">
  <main class="nw-card">
    <header class="nw-head">
      <div class="nw-logo">N</div>
      <h1 class="nw-title">Narwhal IDP</h1>
      <p class="nw-sub"><#nested "header"></p>
    </header>

    <#if displayMessage && message?? && message.summary?has_content>
      <div class="nw-alert nw-alert-${message.type}">
        <span>${kcSanitize(message.summary)?no_esc}</span>
      </div>
    </#if>

    <#nested "form">

    <#if displayInfo>
      <div class="nw-info"><#nested "info"></div>
    </#if>
  </main>
</body>
</html>
</#macro>
