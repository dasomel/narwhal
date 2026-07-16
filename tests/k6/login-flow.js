import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  insecureSkipTLSVerify: true,
  scenarios: {
    ramping_vus: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 1 },
        { duration: '1m', target: 5 },
        { duration: '30s', target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.01'],
  },
};

const BASE = __ENV.K6_BASE_URL || 'https://portal.local.narwhal.internal';
const USERNAME = __ENV.K6_USERNAME;
const PASSWORD = __ENV.K6_PASSWORD;

const KEYCLOAK = BASE.replace('portal.', 'keycloak.');

export default function () {
  // Fresh session every iteration: the per-VU cookie jar keeps both the portal
  // session and the Keycloak SSO cookie, so without clearing, iterations 2+
  // skip the login form entirely and stop exercising Keycloak.
  const jar = http.cookieJar();
  jar.clear(BASE);
  jar.clear(KEYCLOAK);

  // 1. GET CSRF Token
  const csrfRes = http.get(`${BASE}/api/auth/csrf`);
  const csrfToken = csrfRes.json('csrfToken');

  // 2. POST to AuthJS Keycloak signin
  const signinRes = http.post(`${BASE}/api/auth/signin/keycloak`, {
    csrfToken: csrfToken,
    callbackUrl: '/',
  });

  // Extract Keycloak login action URL
  const actionMatch = signinRes.body.match(/action="([^"]+)"/);
  check(actionMatch, { 'keycloak login form found': (m) => m !== null });
  if (actionMatch && actionMatch[1]) {
    let actionUrl = actionMatch[1].replace(/&amp;/g, '&');

    // 3. POST to Keycloak login
    http.post(actionUrl, {
      username: USERNAME,
      password: PASSWORD,
      credentialId: '',
    });
  }

  // 4. Verify login by accessing portal root and session
  const rootRes = http.get(`${BASE}/`);
  check(rootRes, {
    'root is 200': (r) => r.status === 200,
  });

  const sessionRes = http.get(`${BASE}/api/auth/session`);
  check(sessionRes, {
    'session contains @': (r) => r.body.includes('@'),
  });

  sleep(1);
}
