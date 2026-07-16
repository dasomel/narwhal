import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  insecureSkipTLSVerify: true,
  scenarios: {
    ramping_vus: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 10 },
        { duration: '2m', target: 50 },
        { duration: '30s', target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<800'],
    http_req_failed: ['rate<0.01'],
  },
};

const BASE = __ENV.K6_BASE_URL || 'https://portal.local.narwhal.internal';
const USERNAME = __ENV.K6_USERNAME;
const PASSWORD = __ENV.K6_PASSWORD;

export function setup() {
  const jar = http.cookieJar();
  
  const csrfRes = http.get(`${BASE}/api/auth/csrf`);
  const csrfToken = csrfRes.json('csrfToken');

  const signinRes = http.post(`${BASE}/api/auth/signin/keycloak`, {
    csrfToken: csrfToken,
    callbackUrl: '/',
  });

  const actionMatch = signinRes.body.match(/action="([^"]+)"/);
  if (actionMatch && actionMatch[1]) {
    let actionUrl = actionMatch[1].replace(/&amp;/g, '&');
    http.post(actionUrl, {
      username: USERNAME,
      password: PASSWORD,
      credentialId: '',
    });
  }

  const cookies = jar.cookiesForURL(BASE);
  const sessionCookies = {};
  for (const name in cookies) {
    if (name.includes('authjs.session-token')) {
      sessionCookies[name] = cookies[name][0];
    }
  }
  return { cookies: sessionCookies };
}

export default function (data) {
  const jar = http.cookieJar();
  for (const name in data.cookies) {
    jar.set(BASE, name, data.cookies[name]);
  }

  const reqs = [
    { method: 'GET', url: `${BASE}/` },
    { method: 'GET', url: `${BASE}/api/auth/session` },
    { method: 'GET', url: `${BASE}/api/metrics` },
    { method: 'GET', url: `${BASE}/api/catalog` },
    { method: 'GET', url: `${BASE}/api/architecture` },
    { method: 'GET', url: `${BASE}/api/namespaces` },
  ];

  const responses = http.batch(reqs);

  for (const res of responses) {
    check(res, {
      'status is 200': (r) => r.status === 200,
    });
  }

  sleep(0.5);
}
