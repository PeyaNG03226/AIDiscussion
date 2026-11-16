FROM python:3.12-slim

# 用 uv 安装依赖，镜像里不需要额外的 Python 工具链
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    AIDISCUSSION_API_HOST=0.0.0.0 \
    AIDISCUSSION_API_PORT=8000 \
    AIDISCUSSION_DATA_DIR=/data

WORKDIR /app

# 先只装依赖，利用 Docker 层缓存：源码改动时不必重装
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["aidiscussion", "--serve"]
