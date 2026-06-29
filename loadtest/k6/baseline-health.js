// loadtest/k6/baseline-health.js
// 场景 A:/health 阶梯加压。只经 nginx + uvicorn(不碰 DB),量框架请求处理天花板。
import http from 'k6/http';
import { scenarioOptions, target, record429 } from './lib/config.js';

const HEALTH_PATH = __ENV.LOADTEST_HEALTH_PATH || '/api/v1/health';

export const options = scenarioOptions(__ENV, 'baseline_health');

export default function () {
  const t = target(__ENV, HEALTH_PATH);
  const res = http.get(t.url, t.params);
  record429(res);
}
