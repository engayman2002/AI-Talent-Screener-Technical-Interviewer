# Multi-Stage Non-Root Production Dockerfile for Backend Clean Architecture Services

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml ./
COPY src ./src

RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    TS_NODE_ID=api-node-1 \
    TS_SQLITE_DSN=/var/lib/talent_screener/runtime.db \
    DB_DSN=/var/lib/talent_screener/runtime.db \
    DEMO_SEED_ENABLED=true

RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --shell /usr/sbin/nologin --create-home appuser \
    && mkdir -p /app /var/lib/talent_screener \
    && chown -R appuser:appgroup /app /var/lib/talent_screener

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY --chown=appuser:appgroup src ./src

RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels talent-screener \
    && rm -rf /wheels

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3); assert r.status == 200"]

CMD ["python", "-m", "talent_screener.composition_root.main", "--host", "0.0.0.0", "--port", "8000"]
