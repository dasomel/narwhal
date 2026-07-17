import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  insecureSkipTLSVerify: true,
  maxRedirects: 3,
  scenarios: {
    ramping_vus: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 10 },
        { duration: '2m', target: 30 },
        { duration: '30s', target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1500'],
    http_req_failed: ['rate<0.05'],
  },
};

const DOMAIN = __ENV.K6_DOMAIN || 'local.narwhal.internal';
const URLS = [
  `https://grafana.${DOMAIN}/`,
  `https://harbor.${DOMAIN}/`,
  `https://gitea.${DOMAIN}/`,
  `https://argocd.${DOMAIN}/`,
  `https://keycloak.${DOMAIN}/realms/narwhal/.well-known/openid-configuration`,
];

export default function () {
  const responses = http.batch(URLS.map(u => ({ method: 'GET', url: u })));

  responses.forEach((res, index) => {
    const url = URLS[index];
    if (url.includes('openid-configuration')) {
      check(res, { 'openid is 200': (r) => r.status === 200 });
    } else {
      check(res, {
        'status is 200, 302, 303, or 401': (r) => [200, 302, 303, 401].includes(r.status),
      });
    }
  });

  sleep(0.5);
}
