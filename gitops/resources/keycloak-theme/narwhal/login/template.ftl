<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html lang="${(locale.currentLanguageTag)!'en'}">
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
  <#-- deep arctic-ocean horizon under the aurora -->
  <svg class="nw-waves" viewBox="0 0 1440 200" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <path fill="#0e7490" fill-opacity="0.28" d="M0 96 C240 140 480 52 720 84 C960 116 1200 150 1440 92 L1440 200 L0 200 Z"/>
    <path fill="#155e75" fill-opacity="0.34" d="M0 128 C240 104 480 158 720 124 C960 92 1200 138 1440 124 L1440 200 L0 200 Z"/>
    <path fill="#083344" fill-opacity="0.5" d="M0 158 C260 138 520 176 760 158 C1000 140 1220 168 1440 156 L1440 200 L0 200 Z"/>
  </svg>

  <main class="nw-card">
    <header class="nw-head">
      <span class="nw-logo" aria-hidden="true">
        <svg class="nw-whale" viewBox="0 0 64 48" fill="none" xmlns="http://www.w3.org/2000/svg">
          <#-- spiral tusk -->
          <line x1="42" y1="19" x2="62" y2="3" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>
          <g stroke="currentColor" stroke-width="1.1" stroke-linecap="round" opacity="0.7">
            <line x1="45" y1="16.5" x2="47.6" y2="18.4"/>
            <line x1="49" y1="13.5" x2="51.6" y2="15.4"/>
            <line x1="53" y1="10.5" x2="55.6" y2="12.4"/>
            <line x1="57" y1="7.5" x2="59.6" y2="9.4"/>
          </g>
          <#-- water spout -->
          <g stroke="currentColor" stroke-width="1.4" stroke-linecap="round" opacity="0.85">
            <line x1="13" y1="14" x2="13" y2="9"/>
            <line x1="10" y1="15" x2="8.5" y2="11"/>
            <line x1="16" y1="15" x2="17.5" y2="11"/>
          </g>
          <#-- body -->
          <path d="M7 29 C7 19 19 15 31 17 C41 18.6 47 23 46 30 C45.3 35 39 37.6 31 37.2 C21 36.6 13 36.6 9 33.6 C7.6 32.5 7 31 7 29 Z" fill="currentColor"/>
          <#-- tail fluke -->
          <path d="M7 29 L0 23 L3.5 29 L0 35 Z" fill="currentColor"/>
          <#-- pectoral fin -->
          <path d="M23 36.8 C24 41.6 26 43.6 27 43.6 C27 40.6 27 38.6 28 36.6 Z" fill="currentColor" opacity="0.85"/>
          <#-- eye -->
          <circle cx="17" cy="27" r="1.8" fill="#0b1220"/>
        </svg>
      </span>
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

    <p class="nw-foot">Internal Developer Platform</p>
  </main>
</body>
</html>
</#macro>
