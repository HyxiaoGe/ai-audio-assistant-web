// loadtest/k6/lib/config.js
// 压测共享配置:全部环境特定值经 k6 __ENV 注入,脚本内不硬编码 IP/Host/密钥。
import http from 'k6/http';
import { Rate } from 'k6/metrics';

// 429(限流命中)不算故障——它是"应对策略生效"的证据,单独计量。
// 把 2xx 与 429 都标记为"非失败",从 http_req_failed 中排除 429。
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 429));

export const rate429 = new Rate('rate_429');

export function record429(res) {
  rate429.add(res.status === 429);
}

// 必填环境变量:缺失/空即抛,绝不内置默认 IP/Host。
export function requireEnv(env, name) {
  const v = env[name];
  if (v === undefined || v === '') {
    throw new Error(`缺少必填环境变量 ${name}(见 loadtest/README.md)`);
  }
  return v;
}

// 解析阶梯:LOADTEST_STAGES="10:45,25:45,50:45" -> ramping-arrival-rate 的 stages。
// 每段 "目标RPS:稳压秒数"。
export function rampStages(env) {
  const raw = requireEnv(env, 'LOADTEST_STAGES');
  return raw.split(',').map((seg) => {
    const [rate, dur] = seg.split(':');
    const r = Number(rate);
    const d = Number(dur);
    if (!Number.isFinite(r) || !Number.isFinite(d) || r <= 0 || d <= 0) {
      throw new Error(`非法 LOADTEST_STAGES 段 "${seg}",应为 "RPS:秒"`);
    }
    return { target: r, duration: `${d}s` };
  });
}

// 目标 URL + Host 头(直连 :80 绕 CF,靠 Host 头路由)。
export function target(env, path) {
  const base = requireEnv(env, 'LOADTEST_BASE_URL'); // 如 http://192.168.1.11
  const host = requireEnv(env, 'LOADTEST_HOST');     // nginx 路由用 Host 头
  return {
    url: `${base}${path}`,
    params: { headers: { Host: host }, tags: { path } },
  };
}

function p95Ceiling(env) {
  const v = env.LOADTEST_P95_MS;
  return v ? Number(v) : 1500;
}

// 统一构造一条场景的 options:ramping-arrival-rate + 三类阈值 + abortOnFail。
export function scenarioOptions(env, scenarioName) {
  const preVUs = Number(env.LOADTEST_PREALLOC_VUS || 50);
  const maxVUs = Number(env.LOADTEST_MAX_VUS || 2000);
  return {
    scenarios: {
      [scenarioName]: {
        executor: 'ramping-arrival-rate',
        startRate: 0,
        timeUnit: '1s',
        preAllocatedVUs: preVUs,
        maxVUs,
        stages: rampStages(env),
      },
    },
    thresholds: {
      // 错误率(已排除 429)越 5% 即中止整轮——客户端侧硬熄火(防线①)。
      http_req_failed: [{ threshold: 'rate<0.05', abortOnFail: true }],
      // p95 延迟越线即中止。
      http_req_duration: [{ threshold: `p(95)<${p95Ceiling(env)}`, abortOnFail: true }],
      // 限流命中率:仅记录,不熄火(无 abortOnFail),报告里看它随 RPS 上升。
      rate_429: ['rate>=0'],
    },
  };
}
