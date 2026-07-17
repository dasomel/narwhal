import http from 'k6/http';
import { check, sleep } from 'k6';
import { keycloakLogin } from './lib/auth.js';

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
const KEYCLOAK = BASE.replace('portal.', 'keycloak.');
const USERNAME = __ENV.K6_USERNAME;
const PASSWORD = __ENV.K6_PASSWORD;

export default function () {
  // Fresh session every iteration: the per-VU cookie jar keeps both the portal
  // session and the Keycloak SSO cookie, so without clearing, iterations 2+
  // skip the login form entirely and stop exercising Keycloak.
  const jar = http.cookieJar();
  jar.clear(BASE);
  jar.clear(KEYCLOAK);

  const actionMatch = keycloakLogin(BASE, USERNAME, PASSWORD);
  check(actionMatch, { 'keycloak login form found': (m) => m !== null });

  // Verify login by accessing portal root and session
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
