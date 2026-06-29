# 压测装置 Runbook

摸清公开只读路径高并发拐点 + 定位先崩哪层。**v1 只测量不调优**。
设计见 `../docs/superpowers/specs/2026-06-30-load-testing-design.md`。

## ⚠️ 安全前提(必读)
- dev 与生产**同一台机**。压测在压全机器。生产邻居探针(`monitor/sample.sh`)是硬底线:邻居一劣化立即 Ctrl-C 熄火,**保生产高于拿数据**。
- 必须**绕开 Cloudflare 直连源站**(打 `http://<box>:80` + 正确 Host 头),不经公网 CF。
- 选生产**空闲时间窗**,全程人工盯监控,**绝不无人值守跑高档位**。

## 0. 前置工具
```bash
brew install k6 shellcheck
```

## 1. 环境变量(全部经此注入,脚本零硬编码)

| 变量 | 必填 | 默认值 | 示例 / 说明 |
|------|------|--------|-------------|
| `LOADTEST_BASE_URL` | ✅ | — | `http://192.168.1.11` 直连源站,绕 CF |
| `LOADTEST_HOST` | ✅ | — | nginx 路由 Host 头,pre-flight 第 3 步确认 |
| `LOADTEST_STAGES` | ✅ | — | `10:45,25:45,50:45,100:45,200:45`(RPS:秒段) |
| `LOADTEST_HEALTH_PATH` | | `/api/v1/health` | 场景 A 路径 |
| `LOADTEST_PUBLIC_PATH` | ✅ | — | `/api/v1/public/tasks/<真实公开任务id>` |
| `LOADTEST_P95_MS` | | `1500` | p95 熄火阈值(ms),防线① |
| `LOADTEST_PREALLOC_VUS` | | `50` | ramping-arrival-rate 预分配 VU 数 |
| `LOADTEST_MAX_VUS` | | `2000` | 最大 VU 上限 |
| `LOADTEST_SSH` | ✅ | — | `dev`(~/.ssh/config Host 别名) |
| `LOADTEST_API_CONTAINER` | ✅ | — | `docker ps` 落实的 API 容器名 |
| `LOADTEST_PG_CONTAINER` | ✅ | — | `docker ps` 落实的 PostgreSQL 容器名 |
| `LOADTEST_REDIS_CONTAINER` | ✅ | — | `docker ps` 落实的 Redis 容器名 |
| `LOADTEST_NEIGHBOR_URL` | ✅ | — | `https://<同机生产应用>/health` |
| `LOADTEST_SAMPLE_INTERVAL` | | `3` | 采样间隔(秒) |
| `LOADTEST_SAMPLE_OUT` | | 自动带时间戳 | TSV 输出路径,如 `loadtest/reports/sample-YYYYMMDD-HHMMSS.tsv` |
| `LOADTEST_CPU_MAX` | | `85` | CPU% 告警阈值(防线②) |
| `LOADTEST_API_MEM_MAX_MB` | | `350` | API 容器内存告警阈值(MB,对 384m 上限留余量) |
| `LOADTEST_PG_CONN_MAX_PCT` | | `80` | PG 连接占比告警阈值(%) |
| `LOADTEST_NEIGHBOR_MAX_MS` | | `1000` | 邻居探针延迟告警阈值(ms,防线③) |

```bash
# 压测侧(k6)
export LOADTEST_BASE_URL=http://192.168.1.11
export LOADTEST_HOST=<nginx 路由 Host>          # pre-flight 第 3 步确认
export LOADTEST_STAGES=10:45,25:45,50:45,100:45,200:45
export LOADTEST_HEALTH_PATH=/api/v1/health
export LOADTEST_PUBLIC_PATH=/api/v1/public/tasks/<真实公开任务id>
# 监控侧(ssh)
export LOADTEST_SSH=dev
export LOADTEST_API_CONTAINER=<docker ps 落实>
export LOADTEST_PG_CONTAINER=<docker ps 落实>
export LOADTEST_REDIS_CONTAINER=<docker ps 落实>
export LOADTEST_NEIGHBOR_URL=https://<同机生产应用>/health
```

> ⚠️ **k6 v2 注意**:k6 v2 默认**不**把 shell/`export` 的环境变量注入 `__ENV`。本 runbook 所有 `k6 run`/`k6 inspect` 命令都带 `--include-system-env-vars` 以读取上面 `export` 的变量(或改用每个变量 `-e KEY=val`)。漏了该 flag 时 `requireEnv` 会 fail-closed 抛「缺少必填环境变量」——是安全行为,但命令会跑不起来。`config.test.js` 例外(用字面量、不读 `__ENV`,无需 flag)。

## 2. Pre-flight(正式加压前必做,对应 spec §8)
1. `k6 version` 可用。
2. 局域网连通 + 绕 CF 证明:
   `curl -s -D- -o /dev/null -H "Host: $LOADTEST_HOST" "$LOADTEST_BASE_URL/api/v1/health"`
   → 期望 200 且响应头含 `x-app-version`(证明打到源站 api 经 nginx)。直连源站时实际路由以 nginx 为准,若 nginx 另设裸 /health 健康检查位则按实际改 `LOADTEST_HEALTH_PATH`。
3. 核实 Host + path 路由:对 `/api/v1/health` 与 `$LOADTEST_PUBLIC_PATH` 各 curl 一次,
   确认命中正确容器、公开路径返回**真实数据(非 404)**。
4. `docker ps` 落实三个容器名;确认 `ssh $LOADTEST_SSH docker ps` 可用。
5. 低速 dry-run:以下命令覆盖档位为 5 RPS×10s,务必在放开全量前先跑这个:
   ```bash
   # 低速 dry-run(覆盖档位为 5 RPS×10s,务必在放开全量前先跑这个)
   LOADTEST_STAGES=5:10 k6 run --include-system-env-vars loadtest/k6/baseline-health.js
   LOADTEST_STAGES=5:10 k6 run --include-system-env-vars loadtest/k6/public-read.js
   ```
   确认曲线有数、TSV 在写后,再进行下方全量档位压测。
6. 故意 trip abort:临时 `LOADTEST_P95_MS=1` 跑一次,确认 k6 因越线**自动中止**(验证防线①)。
7. 记录空载基线读数后,再正式跑。

## 3. 启动监控(另开一个终端,先于 k6 启动)
> 该新终端需重新 `export` 同一批监控侧 `LOADTEST_*` 变量(env 不跨终端;否则 `sample.sh` 的 `:?` 守卫会 fail-closed 报缺变量)。
```bash
bash loadtest/monitor/sample.sh
# 红字告警出现即考虑 Ctrl-C k6
```

## 4. 跑压测(分别跑两条场景)
```bash
# 场景 A:框架基线
k6 run --include-system-env-vars --summary-export loadtest/reports/baseline-$(date +%H%M%S).json loadtest/k6/baseline-health.js
# 场景 B:公开读路径
k6 run --include-system-env-vars --summary-export loadtest/reports/public-$(date +%H%M%S).json loadtest/k6/public-read.js
```
低速 dry-run 用 `LOADTEST_STAGES=5:10` 覆盖即可。

## 5. 读结果
- k6 终端:每场景 p95/p99、RPS(目标 vs 实达)、`http_req_failed`、`rate_429`。
- 监控 TSV(`reports/sample-*.tsv`):按时间线对齐,看哪个体征先撞阈值。
- **429 不是故障**,是限流在生效——记录它在多少 RPS 介入。
- 把结论填进 `reports/REPORT_TEMPLATE.md`。

## 6. 熄火操作
- k6 侧:`Ctrl-C`(或等 abortOnFail 自动停)。
- 监控红字告警 / 邻居探针变慢或失败:**立即 Ctrl-C k6**,先停压再看。
