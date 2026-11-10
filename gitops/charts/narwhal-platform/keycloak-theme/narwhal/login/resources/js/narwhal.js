/* Narwhal IDP login theme runtime. Loaded synchronously from <head> (theme.properties `scripts`). */

// 1) Follow the Portal's theme. The Portal writes `narwhal-theme=dark|light` on the shared parent
//    domain (see portal/src/components/theme-toggle.tsx). No cookie means light, the Portal default.
//    This runs before first paint, so there is no flash. pf-v5-theme-dark switches PatternFly's own
//    component tokens; nw-dark switches the Portal token block in narwhal.css.
(function () {
  var m = document.cookie.match(/(?:^|;\s*)narwhal-theme=(dark|light)/);
  if (m && m[1] === "dark") {
    document.documentElement.classList.add("pf-v5-theme-dark", "nw-dark");
  }
})();

// 2) Explicit Enter handling on the sign-in form. Implicit submission exists, but the first Enter
//    is routinely swallowed by password-manager overlays and by IME composition (isComposing /
//    legacy keyCode 229 on Korean input). Enter on username moves to password; Enter on password
//    clicks the real submit button, so its name=login pair is posted like a tap.
document.addEventListener("DOMContentLoaded", function () {
  // D1: select bundled copy by the document language. Cost: duplicate four short strings in JS
  // and message bundles; R339 pins their parity. Escape hatch: switch back to server-rendered
  // message data attributes if Keycloak exposes a stable login-header hook.
  // D2: no CSS has-selector here or in the CSS. Browsers without it would throw on querySelector and abort
  // this whole handler (including the IME Enter fix below); html.nw-split is the CSS hook instead.
  var loginForm = document.getElementById("kc-form-login");
  var login = loginForm && loginForm.closest ? loginForm.closest(".pf-v5-c-login__container") : null;
  var header = document.getElementById("kc-header-wrapper");
  if (login && header) {
    var brand = document.createElement("aside");
    brand.className = "nw-login-brand";
    brand.setAttribute("aria-label", "Narwhal IDP");
    var top = document.createElement("div");
    top.className = "nw-login-brand-top";
    top.setAttribute("aria-hidden", "true");
    top.textContent = "Narwhal IDP";
    var copy = document.createElement("div");
    copy.className = "nw-login-brand-copy";
    var mascot = document.createElement("div");
    mascot.className = "nw-login-brand-mascot";
    mascot.setAttribute("aria-hidden", "true");
    var headline = document.createElement("p");
    headline.className = "nw-login-copy nw-login-copy-headline";
    headline.setAttribute("data-en", "One portal for\nthe whole platform");
    headline.setAttribute("data-ko", "하나의 포털에서\n플랫폼 전체를 다룹니다");
    var tagline = document.createElement("p");
    tagline.className = "nw-login-copy nw-login-copy-tagline";
    tagline.setAttribute("data-en", "Catalog, deploys, observability and policy behind one SSO.");
    tagline.setAttribute("data-ko", "서비스 카탈로그, 배포, 관측, 보안 정책을 SSO 한 번으로.");
    copy.appendChild(mascot);
    copy.appendChild(headline);
    copy.appendChild(tagline);
    var footnote = document.createElement("div");
    footnote.className = "nw-login-brand-footnote";
    footnote.textContent = "Kubernetes · GitOps · OIDC SSO";
    brand.appendChild(top);
    brand.appendChild(copy);
    brand.appendChild(footnote);
    login.insertBefore(brand, login.firstChild);
    document.documentElement.classList.add("nw-split");
    var language = document.documentElement.lang.toLowerCase().indexOf("ko") === 0 ? "ko" : "en";
    Array.prototype.forEach.call(document.querySelectorAll(".nw-login-copy"), function (node) {
      node.textContent = node.getAttribute("data-" + language);
    });
  }

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
});
