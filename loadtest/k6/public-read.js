// loadtest/k6/public-read.js
// 场景 B:公开只读路径 阶梯加压。经 nginx + uvicorn + PostgreSQL + Redis,
// 量真实匿名读路径天花板。零鉴权、零副作用。
// LOADTEST_PUBLIC_PATH 必填(内含真实任务 id),无默认值——避免误打 404 失真。
import http from 'k6/http';
import { scenarioOptions, target, record429, requireEnv } from './lib/config.js';

const READ_PATH = requireEnv(__ENV, 'LOADTEST_PUBLIC_PATH');

export const options = scenarioOptions(__ENV, 'public_read');

export default function () {
  const t = target(__ENV, READ_PATH);
  const res = http.get(t.url, t.params);
  record429(res);
}
