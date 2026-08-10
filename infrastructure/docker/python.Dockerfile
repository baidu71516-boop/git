FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/workspace/.venv/bin:$PATH"

RUN groupadd --system app && useradd --system --gid app --home-dir /workspace app \
    && pip install --no-cache-dir uv==0.12.3

WORKDIR /workspace
COPY . .
RUN uv sync --frozen --all-packages --no-dev \
    && mkdir -p /data/imports \
    && chown -R app:app /workspace /data/imports \
    && chmod 0700 /data/imports

USER app
