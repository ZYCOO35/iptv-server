# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /app
# COPY . .

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

COPY --from=builder /wheels /wheels
COPY requirements.txt .
# COPY --from=builder /app /app

RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt

RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash appuser

RUN mkdir -p /app/data && chmod 777 /app/data

USER appuser

WORKDIR /app

EXPOSE 10086

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10086"]