import http from 'k6/http';

// Drive the portal's NextAuth -> Keycloak OIDC login end to end and leave the
// per-VU cookie jar holding a valid (chunked) session cookie. Shared by
// login-flow.js (measures the login itself) and portal-browse.js setup()
// (needs one session to reuse) so the flow lives in exactly one place.
//
// Returns the Keycloak login form's action match (null if the form was not
// served) so callers can assert on it.
export function keycloakLogin(base, username, password) {
  const csrfToken = http.get(`${base}/api/auth/csrf`).json('csrfToken');

  const signinRes = http.post(`${base}/api/auth/signin/keycloak`, {
    csrfToken,
    callbackUrl: '/',
  });

  const actionMatch = signinRes.body.match(/action="([^"]+)"/);
  if (actionMatch && actionMatch[1]) {
    const actionUrl = actionMatch[1].replace(/&amp;/g, '&');
    http.post(actionUrl, { username, password, credentialId: '' });
  }
  return actionMatch;
}
