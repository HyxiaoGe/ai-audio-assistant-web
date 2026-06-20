from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: Literal["development", "staging", "production"] = Field(default="development")
    DEBUG: bool = Field(default=True)

    # API 外部访问地址（用于生成媒体文件 URL）
    API_BASE_URL: str | None = Field(default="http://localhost:8000")

    DATABASE_URL: str | None = Field(default=None)
    REDIS_URL: str | None = Field(default=None)

    JWT_SECRET: str | None = Field(default=None)
    JWT_ALGORITHM: str | None = Field(default=None)

    # 静态数据字段级加密密钥（Fernet）。用于加密落库的密钥/令牌（如 OAuth token）。
    # 生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # 支持逗号分隔多把密钥以便轮换（第一把加密，全部用于解密）。生产环境必须设置。
    FIELD_ENCRYPTION_KEY: str | None = Field(default=None)

    # SQLAlchemy async 连接池大小。默认 5+10 偏小，突发并发（开页齐射认证请求）下易排队；
    # 单 API worker 共享此一池，适度调大以容纳一次页面的并发齐射。
    DB_POOL_SIZE: int = Field(default=10)
    DB_MAX_OVERFLOW: int = Field(default=20)

    # Auth Service (统一认证)
    AUTH_SERVICE_URL: str = Field(default="http://localhost:8100")
    AUTH_SERVICE_JWKS_URL: str | None = Field(default=None)
    # 服务间内部调用（/auth/userinfo、/auth/profile、JWKS）的 auth-service 基址。生产/dev 应指向
    # LAN（如 http://192.168.1.11:8100），避免绕公网 cloudflared 隧道——userinfo 经隧道
    # p50 ~1.2s，走 LAN 仅 ~17ms。留空则回退 AUTH_SERVICE_URL，向后兼容。
    # JWKS 取用也优先此内部基址（见 resolved_auth_jwks_url）：经公网拉 JWKS 实测 ~1.5s/次，
    # 300s 缓存一过期、并发认证请求各拉一遍即造成「开页齐卡」；走 LAN 仅 ~13ms（keys 一致，
    # 不影响 issuer/RS256 签名校验）。
    AUTH_SERVICE_INTERNAL_URL: str | None = Field(default=None)

    CONFIG_CENTER_DB_ENABLED: bool = Field(default=True)
    CONFIG_CENTER_CACHE_TTL: int = Field(default=60)
    # 用户默认免费 ASR 额度（秒），1小时 = 3600秒
    DEFAULT_USER_FREE_QUOTA_SECONDS: int = Field(default=3600)

    MINIO_ENDPOINT: str | None = Field(default=None)
    MINIO_ACCESS_KEY: str | None = Field(default=None)
    MINIO_SECRET_KEY: str | None = Field(default=None)
    MINIO_BUCKET: str | None = Field(default=None)
    MINIO_USE_SSL: bool | None = Field(default=None)

    COS_REGION: str | None = Field(default=None)
    COS_BUCKET: str | None = Field(default=None)
    COS_SECRET_ID: str | None = Field(default=None)
    COS_SECRET_KEY: str | None = Field(default=None)
    COS_ENDPOINT: str | None = Field(default=None)
    COS_USE_SSL: bool | None = Field(default=True)
    COS_PUBLIC_READ: bool | None = Field(default=False)

    OSS_ENDPOINT: str | None = Field(default=None)
    OSS_REGION: str | None = Field(default=None)
    OSS_BUCKET: str | None = Field(default=None)
    OSS_USE_SSL: bool | None = Field(default=True)

    TOS_ENDPOINT: str | None = Field(default=None)
    TOS_REGION: str | None = Field(default=None)
    TOS_BUCKET: str | None = Field(default=None)
    TOS_ACCESS_KEY: str | None = Field(default=None)
    TOS_SECRET_KEY: str | None = Field(default=None)

    TENCENT_SECRET_ID: str | None = Field(default=None)
    TENCENT_SECRET_KEY: str | None = Field(default=None)
    TENCENT_REGION: str | None = Field(default=None)
    TENCENT_ASR_APP_ID: str | None = Field(default=None)
    TENCENT_ASR_ENGINE_MODEL_TYPE: str | None = Field(default=None)
    TENCENT_ASR_ENGINE_MODEL_TYPE_FILE_FAST: str | None = Field(default=None)
    TENCENT_ASR_CHANNEL_NUM: int | None = Field(default=None)
    TENCENT_ASR_SOURCE_TYPE: int | None = Field(default=None)
    TENCENT_ASR_RES_TEXT_FORMAT: int | None = Field(default=None)
    TENCENT_ASR_SPEAKER_DIA: int | None = Field(default=None)
    TENCENT_ASR_SPEAKER_NUMBER: int | None = Field(default=None)
    TENCENT_ASR_POLL_INTERVAL: int | None = Field(default=None)
    TENCENT_ASR_MAX_WAIT_SECONDS: int | None = Field(default=None)
    ALIYUN_ACCESS_KEY_ID: str | None = Field(default=None)
    ALIYUN_ACCESS_KEY_SECRET: str | None = Field(default=None)
    ALIYUN_NLS_APP_KEY: str | None = Field(default=None)
    VOLC_ASR_APP_ID: str | None = Field(default=None)
    VOLC_ASR_ACCESS_TOKEN: str | None = Field(default=None)
    VOLC_ASR_RESOURCE_ID: str | None = Field(default=None)
    VOLC_ASR_MODEL_NAME: str | None = Field(default=None)
    VOLC_ASR_MODEL_VERSION: str | None = Field(default=None)
    VOLC_ASR_LANGUAGE: str | None = Field(default=None)
    VOLC_ASR_ENABLE_ITN: bool | None = Field(default=None)
    VOLC_ASR_SHOW_UTTERANCES: bool | None = Field(default=None)
    VOLC_ASR_POLL_INTERVAL: int | None = Field(default=None)
    VOLC_ASR_MAX_WAIT_SECONDS: int | None = Field(default=None)

    # OpenRouter 仅保留给 RAG embedding（embedder 仍直连 openrouter
    # 的 text-embedding-3-small；切到 LiteLLM 是 Phase 3.5 的事）。
    # 其它 LLM provider（doubao/qwen/moonshot/deepseek）的 env 已下线，
    # 全部由 LiteLLM Proxy 负责路由。
    OPENROUTER_API_KEY: str | None = Field(default=None)
    OPENROUTER_BASE_URL: str | None = Field(default="https://openrouter.ai/api/v1")
    OPENROUTER_HTTP_REFERER: str | None = Field(default=None)
    OPENROUTER_APP_TITLE: str | None = Field(default=None)

    # LiteLLM Proxy
    LITELLM_BASE_URL: str = Field(default="http://litellm-proxy:4000")
    LITELLM_API_KEY: str | None = Field(default=None)
    LITELLM_MODEL: str = Field(default="chat-default")
    LITELLM_MAX_TOKENS: int = Field(default=4096)

    # 转写润色的并发 LLM 调用上限。润色把长转写按时间窗/段数切成多组、各组独立调一次
    # deepseek-chat。有界并发压缩总耗时；上限必须 < proxy_llm 熔断阈值（failure_threshold=5），
    # 使「一整波同时失败」也不足以把熔断打 OPEN、连累随后同走 proxy_llm 的摘要生成。
    # 实测 647 段/14 组(每组 50 段)在并发 3 下 ~16min：故默认提到 4（仍 <5 保熔断不变式），
    # 配合下方更小的分组（每组调用稳定落在 120s httpx 超时内、免去静默超时重试）显著提速。
    POLISH_CONCURRENCY: int = Field(default=4)

    # 单个润色分组的最大片段数。每组拼成一次非流式 chat 调用，httpx 客户端读超时 120s——
    # 非流式响应在生成完成前不回任何字节，故单组生成一旦 >120s 就会静默触发读超时 + @retry
    # 重试（最多 3 次、各 120s），白白浪费一整次超时时长并重解整组。实测每组 50 段时多组
    # 落在 ~110-240s（>120s 即已在超时重试）。下调到 25 段，使单组稳定落在 120s 内、消除
    # 这层重试税，并减小单组输出（降低截断/空返回风险）。分组同时受 window_seconds 约束，
    # 取先到者；密集语音下本上限通常是实际生效的约束。
    POLISH_MAX_SEGMENTS_PER_GROUP: int = Field(default=25)

    # 单组润色在回退原文前的最大尝试次数（含首次）。可重试的失败有两类：① chat 抛异常
    # （超时已在 HTTP 层重试，这里多兜熔断 OPEN 等）；② 响应退化——空 / 无任何 [序号] 行
    # （代理推理吃满 max_tokens 回空最常见，这类「200 但无效」HTTP 层不会重试、过去被 parse
    # 当成"整组无改动"静默回退原文丢润色）。默认 2 = 首次 + 1 次重试：足以救回观测到的瞬时
    # 空返回，又刻意保守——重试经同一 Semaphore 限流，瞬时在途数仍 ≤ POLISH_CONCURRENCY，
    # 不破坏「一波失败不足以打 OPEN 熔断、连累随后摘要」的不变式；代理真宕时重试快速失败再
    # 回退，graceful。设为 1 即关闭重试（恢复旧行为）。
    POLISH_MAX_ATTEMPTS_PER_GROUP: int = Field(default=2)

    # 润色单组重试的线性退避基数（秒）：第 n 次重试前 sleep n×本值，给瞬时空返回/半开熔断
    # 留恢复窗口。退避在 Semaphore 之外 sleep，期间槽位让给其它组。
    POLISH_RETRY_BACKOFF_SECONDS: float = Field(default=1.0)

    # 转写润色（polish）固定使用的内部模型，刻意不跟随用户为「摘要」选择的模型。
    # polish 是机械式 ASR 纠错（错别字/同音字/中英术语/纯语气词置空），不需要重思考模型：
    # 实测重思考模型 doubao-seed-2-0-pro 每次烧 1400+ 思考 token、慢 4 倍却无质量增益（上个真实
    # 任务 polish 占 569s/68%）。25 段实测 deepseek-chat 质量满分（上下文纠错 bird→BERT、正确保留
    # 实质语气段、中英排版更干净）且仅 ~10s，故内部钉死 deepseek-chat。provider 留空或非注册时回落
    # 到默认注册的 proxy 服务（见 worker.tasks.process_youtube._resolve_polish_selection）。
    POLISH_MODEL_ID: str = Field(default="deepseek-chat")
    POLISH_PROVIDER: str = Field(default="proxy")

    # 摘要配图的并发生成上限。摘要按 {{IMAGE:}} 锚点最多生成 max_images=6 张，原为无界一次性
    # 并发 6 张 → 首发即撞 image-service 429（429 不在客户端重试白名单，该张直接终态失败），
    # 累计失败还会触发 image_service 熔断（阈值 5）。有界并发把在途数压到此值以下消除 burst。
    # 比润色更保守（默认 2）：生图路径更脆（429 不重试、单张失败补不回）。上限须 < 熔断阈值 5。
    IMAGE_GEN_CONCURRENCY: int = Field(default=2)

    # 远程 image-service（独立部署的 Gemini 生图服务）
    IMAGE_SERVICE_BASE_URL: str | None = Field(default=None)
    IMAGE_SERVICE_API_KEY: str | None = Field(default=None)
    IMAGE_SERVICE_DEFAULT_MODEL: str = Field(default="gemini-3-pro-image-preview")

    OPENAI_API_KEY: str | None = Field(default=None)
    OPENAI_BASE_URL: str | None = Field(default="https://api.openai.com/v1")

    # 默认关闭:embedding 读端 100% 不存在(无任何检索/搜索消费 RagChunk),且写端在 dev
    # 大半失败,纯浪费延迟+日志+垃圾 usage 行。全文搜索改走 pg_jieba FTS(见 transcript_search)。
    # 待真正建成语义检索(pgvector + 语义 /search)再开。
    RAG_EMBEDDING_ENABLED: bool = Field(default=False)
    RAG_EMBEDDING_PROVIDER: str | None = Field(default="openrouter")
    RAG_EMBEDDING_MODEL: str | None = Field(default="text-embedding-3-small")
    RAG_EMBEDDING_DIM: int | None = Field(default=1536)
    RAG_CHUNK_SIZE: int = Field(default=300)
    RAG_CHUNK_OVERLAP: int = Field(default=50)
    RAG_EMBED_BATCH_SIZE: int = Field(default=64)

    UPLOAD_ALLOWED_EXTENSIONS: str | None = Field(default=None)
    UPLOAD_MAX_SIZE_BYTES: int | None = Field(default=None)
    UPLOAD_PRESIGN_EXPIRES: int | None = Field(default=None)
    MEDIA_DOWNLOAD_EXPIRES: int = Field(default=3600)
    # 短期媒体/SSE 票据有效期（秒）。前端 <img>/<audio>/EventSource 无法带 header，
    # 改用此短票放进 ?token=，避免长效 access JWT 暴露在 URL/代理日志里。
    MEDIA_TOKEN_TTL: int = Field(default=300)

    YOUTUBE_DOWNLOAD_DIR: str | None = Field(default=None)
    YOUTUBE_OUTPUT_TEMPLATE: str | None = Field(default=None)
    YOUTUBE_DOWNLOAD_FORMAT: str | None = Field(default=None)

    # —— yt-dlp 抓取韧性 ——（worker 解析/下载 YouTube 用；国内直连 YouTube 抖动大，需重试+超时）
    # 单连接 socket 读/连超时（秒）。yt-dlp 默认不设上限，慢连接会无限期挂住占满 worker。
    YOUTUBE_SOCKET_TIMEOUT: int = Field(default=30)
    # yt-dlp 自身的下载/分片/解析重试次数（库内重试，针对单次请求内的瞬时网络抖动）。
    YOUTUBE_DOWNLOAD_RETRIES: int = Field(default=5)
    # 应用层对「解析(extract_info)」整体的重试次数(含首次)。解析是创建后最易因 CN 直连超时
    # 误失败的一步；仅对瞬时错误(超时/连接/5xx)重试，私有/删除/地域等永久错误立即失败不空耗。
    YOUTUBE_RESOLVE_MAX_ATTEMPTS: int = Field(default=3)
    # 应用层对「下载」整体的重试次数(含首次)。下载本身已有 yt-dlp 库内重试兜底，故应用层少重试。
    YOUTUBE_DOWNLOAD_MAX_ATTEMPTS: int = Field(default=2)

    # Google OAuth for YouTube API
    GOOGLE_CLIENT_ID: str | None = Field(default=None)
    GOOGLE_CLIENT_SECRET: str | None = Field(default=None)
    YOUTUBE_OAUTH_REDIRECT_URI: str | None = Field(default=None)
    FRONTEND_URL: str = Field(default="http://localhost:3000")

    # CORS 允许的额外 origins（逗号分隔）
    CORS_ORIGINS: str | None = Field(default=None)

    STATS_CURRENCY: str = Field(default="CNY")

    TASK_CLEANUP_DELAY_SECONDS: int = Field(default=300)

    # 按用户每分钟限流（放大成本/抓取的端点；详见 app/core/rate_limit.py）
    RATE_LIMIT_TASK_CREATE_PER_MIN: int = Field(default=20)
    RATE_LIMIT_UPLOAD_PRESIGN_PER_MIN: int = Field(default=30)
    RATE_LIMIT_SUMMARY_COMPARE_PER_MIN: int = Field(default=10)
    RATE_LIMIT_YOUTUBE_SYNC_PER_MIN: int = Field(default=10)
    RATE_LIMIT_PUBLIC_PER_MIN: int = Field(default=60)  # 公开探索端点,按 IP
    RATE_LIMIT_TASK_SEARCH_PER_MIN: int = Field(default=30)  # 转写全文搜索(FTS 查询有成本)

    # PromptHub
    PROMPTHUB_BASE_URL: str | None = Field(default=None)
    PROMPTHUB_API_KEY: str | None = Field(default=None)
    PROMPTHUB_CACHE_TTL: int = Field(default=300)  # seconds
    PROMPTHUB_IMAGE_GEN_PROJECT_ID: str | None = Field(default=None)

    @property
    def resolved_auth_service_internal_url(self) -> str:
        """内部服务间调用应使用的 auth-service 基址（已剥尾部 /）。

        优先 AUTH_SERVICE_INTERNAL_URL（生产/dev 指向 LAN）；未设则回退 AUTH_SERVICE_URL，
        保持向后兼容。用于 userinfo/profile 等内部 HTTP 调用；JWKS 取用见
        resolved_auth_jwks_url（同样优先内部基址）。
        """
        return (self.AUTH_SERVICE_INTERNAL_URL or self.AUTH_SERVICE_URL).rstrip("/")

    @property
    def resolved_auth_jwks_url(self) -> str:
        """JWKS 公钥集的获取 URL（已含 /.well-known/jwks.json 路径）。

        JWKS 校验是「后端↔auth-service」调用，与 userinfo/profile 同类，应优先走 LAN 内部基址：
        经公网 cloudflared 隧道拉 JWKS 实测 ~1.5s/次，而 JWTValidator 缓存仅 300s，一过期、
        并发认证请求各拉一遍（单 worker、库内无 singleflight）即造成「开页齐卡」的尾延迟；
        走 LAN 仅 ~13ms，且两端 keys 一致、不影响 issuer/RS256 签名校验。

        优先级：AUTH_SERVICE_INTERNAL_URL > AUTH_SERVICE_JWKS_URL > AUTH_SERVICE_URL。
        显式 AUTH_SERVICE_JWKS_URL 仅在未配内部基址时作回退（如仅公网可达的环境）。
        """
        if self.AUTH_SERVICE_INTERNAL_URL:
            return f"{self.AUTH_SERVICE_INTERNAL_URL.rstrip('/')}/.well-known/jwks.json"
        if self.AUTH_SERVICE_JWKS_URL:
            return self.AUTH_SERVICE_JWKS_URL
        return f"{self.AUTH_SERVICE_URL.rstrip('/')}/.well-known/jwks.json"

    @model_validator(mode="after")
    def _require_prod_secrets(self) -> Settings:
        """生产环境强制注入必需密钥，缺失即启动失败（fail-fast，不静默降级）。

        仅校验 production；dev/staging 不受影响。这些密钥须由 secrets manager /
        orchestrator 注入，绝不写入镜像或代码库。
        """
        if self.APP_ENV == "production":
            missing: list[str] = []
            if not self.FIELD_ENCRYPTION_KEY:
                missing.append("FIELD_ENCRYPTION_KEY")
            # 媒体/SSE 短票用 JWT_SECRET 自签（HS256）；生产缺失则无法签发安全的媒体 URL。
            if not self.JWT_SECRET:
                missing.append("JWT_SECRET")
            if missing:
                raise ValueError(
                    "生产环境缺少必需密钥（必须由 secrets manager/orchestrator 注入，不得写入镜像）: "
                    + ", ".join(missing)
                )
        return self


settings = Settings()
