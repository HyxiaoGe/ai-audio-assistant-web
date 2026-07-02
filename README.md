# AI 音视频助手 · 后端

[![CI](https://github.com/HyxiaoGe/ai-audio-assistant-web/actions/workflows/build-and-deploy.yml/badge.svg)](https://github.com/HyxiaoGe/ai-audio-assistant-web/actions/workflows/build-and-deploy.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)

[English](README_EN.md)

把长音视频内容转化为**可核验、可复用、可发现**的知识卡片 —— 转写、结构化摘要、关键点、待办与配图。

这是产品的**后端**(FastAPI + Celery);前端为独立的 Next.js 应用 `ai-audio-assistant-ui`,鉴权、提示词等经共享服务打通。

## 功能特性

以下均为代码中已落地的能力:

- **多厂商 ASR 转写** —— 腾讯云 / 阿里云 / 火山引擎;支持说话人分离、标准/极速变体,配额感知的智能调度(`ASRScheduler`)。送云前统一转 16k 单声道 mp3,绕开单文件体积上限。
- **转写自动润色** —— 转写与摘要之间的 `polishing` 阶段,用固定高性价比模型做机械式纠错(错别字/术语/标点),分组独立、段数 1:1、失败保留原文。
- **结构化摘要** —— 7 种规范风格 + `auto` 自动识别;概览 / 关键点 / 待办;**多模型并排对比**、版本管理与一键激活、`regenerate`、SSE 流式输出。
- **渐进式配图** —— 摘要完成后异步为概览生成插图(远端 image-service),占位锚点先落库、生图后合并。
- **转写全文检索** —— `GET /api/v1/tasks/search`,PostgreSQL `tsvector` + pg_jieba 中文分词 + GIN 索引 + 应用层高亮。纯词法匹配、零 LLM 成本(语义 / 向量检索为**刻意关闭**的非目标)。
- **YouTube 集成** —— OAuth 连接、订阅同步为「待处理收件箱」、频道浏览、加星、批量自动转写、按链接/关键词直接转写。
- **/discover 发现页(公开)** —— 关键词搜索(yt-dlp `ytsearch`,零配额,≤6h 缓存)、「大家在搜」热门词、按播放量 harvest 的热门推荐;整体受 discover kill-switch 开关。
- **公开探索页(匿名)** —— 浏览管理员公开的已完成任务及其转写/摘要,配短期作用域媒体票据。
- **内容治理** —— 接入 CMS / 腾讯 TMS 审核(`search_query` / `ugc_publish` / `ugc_display` 三场景 × `off` / `shadow` / `enforce` 三模式,enforce 下 fail-closed);人工黑名单/白名单(屏蔽频道、屏蔽词、频道白名单)与频道标记复核队列。
- **成本与配额** —— 管理员按用户成本看板(¥ ASR+配图 / $ LLM,双币种分列不相加)、ASR 月度配额管理与预警。
- **平台能力** —— WebSocket 实时进度、站内通知、用户偏好、运行时配置中心、统计分析、客户端错误上报、管理后台查看用户转写明细。

## 架构概览

| 组件 | 技术 / 说明 |
|------|-------------|
| API | FastAPI(容器监听 `8000`,`docker-compose` 对外映射 `8088`);统一响应体 `{code, message, data, traceId}` |
| 异步 Worker | Celery(`--autoscale=8,1`)+ Celery Beat 定时任务;Redis 作 broker / backend |
| 数据库 | PostgreSQL(asyncpg)+ Alembic 迁移 + pg_jieba 中文分词 |
| 对象存储 | MinIO(开发)/ 腾讯云 COS / 阿里云 OSS / 火山 TOS(生产多厂商) |
| ASR | 腾讯云 / 阿里云 / 火山引擎(厂商直连 SDK) |
| 文本 LLM | **统一经 LiteLLM Proxy**;模型目录直接代理 LiteLLM,增删模型只在 LiteLLM 侧操作 |
| 生图 | 远端 image-service(Gemini 系模型) |
| 鉴权 | auth-service 签发 RS256 JWT,后端经 **JWKS** 校验(SSO) |
| 本地可编辑依赖 | `prompthub-sdk`、`auth-client`(经 `[tool.uv.sources]` 指向工作区上层目录 `../../prompthub`、`../../auth-service`) |

**服务选型(SmartFactory)**:外部服务经 `@register_service` 注册,`SmartFactory` 支持 `health_first`(默认)/ `cost_first` / `performance_first` / `balanced` 策略。已注册:ASR = `tencent` / `aliyun` / `volcengine`,存储 = `minio` / `cos` / `oss` / `tos`,LLM = `proxy`(LiteLLM 统一入口)+ `image_service`。生产中文本 LLM 恒走 `proxy`,ASR 真实调度由配额感知的 `ASRScheduler` 负责。

**任务流水线**:

```
pending → extracting(10%) → transcribing(40%) → polishing(72%) → summarizing(82%) → completed(100%)
                                        │ 任一阶段异常
                                        ▼
                                      failed
```

摘要文字失败不连带整任务失败(转写已可展示);`completed` 之后异步补概览配图。

## 快速开始

### 环境要求

- **Python 3.11+**(容器基镜像 `python:3.11-slim`;ruff 语法目标 `py312`)
- **PostgreSQL**、**Redis**、**FFmpeg**
- 包管理器 **uv**(依赖唯一来源 `pyproject.toml`,不维护 `requirements.txt`)

### 本地开发(直接跑进程)

```bash
cp .env.example .env
uv sync --dev            # 装运行时 + 开发依赖(pytest 等);仅运行时用 uv sync
source .venv/bin/activate

# 启动 API(本地 --reload 默认监听 8000)
uvicorn app.main:app --reload

# 启动 Celery Worker
celery -A worker.celery_app worker --loglevel=info

# 启动 Celery Beat(定时任务)
celery -A worker.celery_app beat --loglevel=info

# 数据库迁移
alembic upgrade head
```

> **lint 提示**:CI 的 lint 门禁是 **ruff**,但 ruff 不在 `uv sync --dev` 的依赖组里(dev 组为历史遗留的 pytest/black/… )。本地要与 CI 对齐,单独 `uvx ruff` 或 `pip install ruff` 即可。

### Docker 方式

`docker-compose.yml` 只定义 4 个服务:`api`、`worker`、`beat`、`migrate`(`migrate` 属 `manual` profile,按需运行)。

> ⚠️ **不是开箱即用的全栈**:PostgreSQL / Redis / MinIO **不在** compose 内(配置指向外部主机),且依赖一个**已存在的外部网络** `ai-audio-network`。请先自备这些外部依赖与网络,再 `up`。

```bash
docker-compose up -d              # 起 api / worker / beat(需外部 PG/Redis/MinIO + 外部网络)
docker compose run --rm migrate   # 手动执行迁移(alembic upgrade head)
```

容器内 api 命令为 `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`,端口映射 `8088:8000`(即 `http://localhost:8088`)。

## 质量门禁与测试

```bash
ruff check app/ worker/ tests/          # lint —— CI 实际门禁
ruff format app/ worker/ tests/         # 格式化
pytest tests/ -v                        # 测试 —— CI 实际门禁
pytest tests/ -v --cov=app --cov=worker # 带覆盖率
```

CI(`.github/workflows/build-and-deploy.yml` 的 build job)实际只跑 **ruff + pytest**;部署 job(`deploy-dev`)仅在 `master` 触发。`pyproject.toml` dev 组里仍列着 `black/isort/flake8/mypy/bandit`,但**均非门禁**,已被 ruff 取代。

## 配置说明

配置由 `pydantic-settings` 的 `Settings` 集中定义(`app/config.py`),从 `.env` 加载。**变量的权威定义在 `app/config.py`;`.env.example` 是常用变量样例(并非全量)**,下表只列关键分组;真实密钥由 secret manager 注入,`.env` 已被 `.gitignore` 忽略,**切勿提交**。

pydantic 层所有项都有默认值;但当 `APP_ENV=production` 时,校验器会**强制**下列密钥,缺失即**启动失败**:

| 变量名 | 说明 |
|---|---|
| `FIELD_ENCRYPTION_KEY` | 落库 OAuth token 等敏感字段的 Fernet 加密密钥;逗号分隔多把可轮换(首把加密、全部解密)。生产必填 |
| `JWT_SECRET` | 媒体 / SSE 短期票据的 HS256 自签密钥。生产必填 |

其余关键分组(完整见 `.env.example`):

| 分组 | 代表变量 | 备注 |
|------|----------|------|
| 数据库 / Redis | `DATABASE_URL`、`REDIS_URL`、`DB_POOL_SIZE`、`DB_MAX_OVERFLOW` | 应用/Worker 运行必需;worker 翻倍须同步减池以护共享 PG |
| 鉴权 | `AUTH_SERVICE_URL`、`AUTH_SERVICE_INTERNAL_URL`、`AUTH_SERVICE_JWKS_URL` | JWKS 优先走内网基址避公网隧道尾延 |
| 对象存储(四选一) | `MINIO_*` / `COS_*` / `OSS_*` / `TOS_*` | 选用哪家配哪组 |
| ASR(三厂商) | `TENCENT_*` / `ALIYUN_*` / `VOLC_ASR_*` | 按凭证自动发现;另有引擎/说话人分离调参 |
| 文本 LLM | `LITELLM_BASE_URL`、`LITELLM_API_KEY`、`LITELLM_MODEL` | 所有 chat/completion 统一经 LiteLLM Proxy |
| 生图 | `IMAGE_SERVICE_BASE_URL`、`IMAGE_SERVICE_API_KEY` | 配图功能必需 |
| 提示词 | `PROMPTHUB_BASE_URL`、`PROMPTHUB_API_KEY` | 摘要/配图提示词唯一活源,无本地回落;未配则相关任务运行时失败(非启动校验强制) |
| 内容审核 | `MODERATION_*_MODE`、`MODERATION_API_KEY` | 三场景三态,**默认全 `off`**;`enforce` 时需 key |
| 发现页 / YouTube | `YOUTUBE_SEARCH_*`、`GOOGLE_CLIENT_ID/SECRET` | 搜索紧超时/缓存/限流;OAuth 凭证 |
| 限流 / 开关 | `RATE_LIMIT_*`、`DEAD_TASK_SWEEP_ENABLED`、`CONFIG_CENTER_DB_ENABLED` | 各端点每分钟限流、巡检与配置中心开关 |

> 注:部分变量(`JWT_SECRET`、`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`、`COS_*`、`MODERATION_*`、`RATE_LIMIT_*` 等)未写入 `.env.example`,以 `app/config.py` 为准;`ENABLE_DOCS` 由 `app/main.py` 直接读取。

## API 概览

全部端点统一挂在 `/api/v1` 前缀下(`app/api/v1/router.py`):

- `/tasks`、`/tasks/search`(转写全文检索)、`/transcripts`、`/summaries`、`/summary-styles`
- `/upload`、`/media`(媒体票据与字节流)、`/ws`(实时进度 WebSocket)
- `/users`、`/notifications`、`/stats`、`/llm`、`/public`(匿名探索)
- `/youtube`(订阅 / 发现 / 搜索 / 回调)、`/asr/quotas`、`/asr/usage`
- `/configs`(配置中心)、`/admin/*`(成本看板、任务明细、屏蔽 / 白名单 / 复核队列)
- `/health`、`/readiness`、`/client-errors`

> 交互式文档(`/docs`、`/redoc`、`/openapi.json`)由 `ENABLE_DOCS` 闸控,**默认关闭**(compose 中显式设为 `false`)。本地查看:启动前设 `ENABLE_DOCS=true`,访问 `http://localhost:8000/docs`。

## 目录结构

```
ai-audio-assistant-web/
├── app/                # FastAPI 应用
│   ├── api/            # 路由(api/v1/*)与依赖注入(deps.py)
│   ├── core/           # SmartFactory / 中间件 / 响应 / 异常 / 监控 / 容错
│   ├── services/       # asr/ llm/ storage/ moderation/ youtube/ … 外部服务实现
│   ├── models/         # SQLAlchemy 模型
│   ├── schemas/        # Pydantic 出入参
│   ├── prompts/        # 提示词模板管理
│   ├── i18n/           # 错误码与多语言文案
│   ├── config.py       # 配置(pydantic-settings)
│   ├── db.py           # 异步会话工厂
│   └── main.py         # 应用装配(create_app)
├── worker/             # Celery 任务与 beat 调度
├── alembic/            # 数据库迁移
├── tests/              # pytest 测试
├── scripts/            # 运维 / 一次性脚本
├── loadtest/           # 压测装置
└── docs/               # 设计与接口文档
```

## 文档索引

| 文档 | 位置 | 说明 |
|------|------|------|
| 工程约定 | `CLAUDE.md` | 目录/服务约定、SmartFactory、编码规范 |
| API 规范 | `docs/API.md` | 端点、请求/响应、错误码 |
| 架构详解 | `docs/ARCH.md` | 服务分层、请求流、SmartFactory |
| 架构决策 | `docs/ADR.md` | 关键技术选型 ADR |
| 产品需求 | `docs/PRD.md` | 产品目标与范围 |
| 常见问题 | `docs/FAQ.md` | 排障与常见问题 |
| RAG 规划 | `docs/RAG_PLAN.md` | 语义检索规划(当前刻意停用,非目标) |
| 分特性设计/计划 | `docs/superpowers/specs/`、`docs/superpowers/plans/` | 按特性归档的 spec 与实现计划 |
| 环境变量样例 | `.env.example` | 全量环境变量(以此为准) |

---

English version: [README_EN.md](README_EN.md)
