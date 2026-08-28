FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.12-slim AS builder

COPY --from=uv /uv /bin/uv
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12-slim AS runtime

RUN groupadd --system iptv && useradd --system --gid iptv --home-dir /app iptv

WORKDIR /app
COPY --from=builder --chown=iptv:iptv /app/.venv /app/.venv
COPY --chown=iptv:iptv app /app/app
COPY --chown=iptv:iptv config/config.example.yaml /app/config/config.example.yaml

ENV PATH="/app/.venv/bin:$PATH"
ENV IPTV_CONFIG_PATH=/app/config/config.yaml

USER iptv
EXPOSE 8889

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8889"]
