FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TZ=UTC

WORKDIR /app

# 换 apt 源到阿里云镜像，避免 Debian 官方 Fastly CDN 在国内不稳定。
# 兼容 Debian 12+ 的 deb822 格式（sources.list.d/debian.sources）和老的 sources.list。
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g; s|http://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi && \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g; s|http://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        nodejs \
        npm \
        curl \
        unzip \
        git \
    && npm install -g @mermaid-js/mermaid-cli \
    && mmdc --version \
    # Install deno for yt-dlp YouTube extraction
    && curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/ \
    && deno --version \
    && rm -rf /var/lib/apt/lists/* /root/.deno

# 把 pyproject 里两个本地依赖（prompthub-sdk / auth-client）替换成 git+https 安装，
# 让 docker build 不依赖 BuildKit additional_contexts（即不依赖本地 sibling 目录）。
# 这跟 ci.yml lint 步骤里的 GIT_DEPS map 逻辑保持一致。
COPY pyproject.toml ./
RUN python - <<'PY'
import pathlib
import tomllib

with open("pyproject.toml", "rb") as f:
    data = tomllib.load(f)

GIT_DEPS = {
    "prompthub-sdk": "prompthub-sdk @ git+https://github.com/HyxiaoGe/prompthub.git@master#subdirectory=sdk",
    "auth-client": "auth-client[fastapi] @ git+https://github.com/HyxiaoGe/auth-service.git@auth-client-v0.2.0#subdirectory=auth-client",
}

requirements = data["project"]["dependencies"]
resolved = [
    GIT_DEPS.get(r.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip(), r)
    for r in requirements
]
pathlib.Path("/tmp/requirements.txt").write_text("\n".join(resolved))
PY
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . .

EXPOSE 8000
