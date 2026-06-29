// loadtest/k6/lib/config.test.js
// 用 `k6 run --vus 1 --iterations 1` 执行。
// 注意:k6 中 default 函数里 throw 会被吞、进程仍退 0;必须用
// exec.test.abort() 才能在断言失败时非零退出(实测退码 108)= 红。
import exec from 'k6/execution';
import { requireEnv, rampStages, scenarioOptions } from './config.js';

function assert(cond, msg) {
  if (!cond) exec.test.abort(`断言失败:${msg}`);
}

export default function () {
  // requireEnv:缺失即抛
  let threw = false;
  try { requireEnv({}, 'NOPE'); } catch (_e) { threw = true; }
  assert(threw, 'requireEnv 应在缺失时抛错');
  assert(requireEnv({ A: 'x' }, 'A') === 'x', 'requireEnv 应返回值');

  // rampStages:解析 "RPS:秒"
  const stages = rampStages({ LOADTEST_STAGES: '10:45,200:30' });
  assert(stages.length === 2, 'stages 应两段');
  assert(stages[0].target === 10 && stages[0].duration === '45s', '首段正确');
  assert(stages[1].target === 200 && stages[1].duration === '30s', '次段正确');

  // rampStages:非法段抛错
  let bad = false;
  try { rampStages({ LOADTEST_STAGES: '10' }); } catch (_e) { bad = true; }
  assert(bad, '非法 stages 应抛错');

  // scenarioOptions:装配 executor + 三类阈值 + abortOnFail
  const opts = scenarioOptions({ LOADTEST_STAGES: '10:10' }, 'unit');
  assert(opts.scenarios.unit.executor === 'ramping-arrival-rate', 'executor 正确');
  assert(opts.thresholds.http_req_failed[0].abortOnFail === true, '错误率阈值带 abortOnFail');
  assert(opts.thresholds.http_req_duration[0].abortOnFail === true, 'p95 阈值带 abortOnFail');
  assert(Array.isArray(opts.thresholds.rate_429), '429 仅记录(无 abortOnFail)');

  console.log('config.test.js 全部断言通过');
}
