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
