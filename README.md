# AI 音视频助手后端

[English](README_EN.md)

面向音视频内容的 AI 助手后端，支持多厂商 LLM/ASR/存储接入，提供转写与结构化摘要。

## 功能简介

- 音视频转写，输出带时间戳的文本片段
- 自动生成结构化摘要（概览 / 关键点 / 行动项）
- 多厂商智能选型（健康度 / 成本 / 性能）
- 成本统计与服务健康监控

## 架构概览

- API：FastAPI
- Worker：Celery + Redis
- 数据库：PostgreSQL
- 存储：MinIO (开发) / COS 或 OSS 或 TOS (生产)
- ASR：腾讯云 / 阿里云 / 火山引擎
- LLM：豆包 / 通义千问 / Moonshot / DeepSeek / OpenRouter

## 快速开始（本地）

前置：Python 3.11+、Redis、PostgreSQL、FFmpeg。

```bash
cp .env.example .env
uv sync
source .venv/bin/activate

# 启动 API
uvicorn app.main:app --reload

# 启动 Worker
celery -A worker.celery_app worker --loglevel=info
```

如需 Docker 方式，参考 `docker-compose.yml`。

## 配置说明

- 环境变量见 `.env.example`
- 多厂商使用与 SmartFactory：`docs/QUICKSTART.md`
- 后端架构与约束：`docs/BACKEND_QUICKSTART.md`
- API 文档：`docs/API.md`

## 测试

```bash
pytest tests/ -v
mypy app/ worker/
flake8 app/ worker/ tests/ --max-line-length=100
```

## 目录结构

- `app/`：FastAPI 应用
- `worker/`：Celery 任务
- `tests/`：pytest 测试
- `docs/`：设计与接口文档
- `alembic/`：数据库迁移

---

English version: `README_EN.md`
