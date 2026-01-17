FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        nodejs \
        npm \
    && npm install -g @mermaid-js/mermaid-cli \
    && mmdc --version \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN python - <<'PY'
import pathlib
import tomllib

with open("pyproject.toml", "rb") as f:
    data = tomllib.load(f)

requirements = data["project"]["dependencies"]
path = pathlib.Path("/tmp/requirements.txt")
path.write_text("\n".join(requirements))
PY
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . .

EXPOSE 8000
