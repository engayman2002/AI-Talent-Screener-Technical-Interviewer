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
    TS_SQLITE_DSN=/var/lib/talent_screener/runtime.db

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
    CMD ["python", "-c", "import asyncio; from talent_screener.composition_root import build_production_container; from talent_screener.presentation.schemas import HttpRequest; b=build_production_container(); r=asyncio.run(b.rest_router.handle(HttpRequest(method='GET', path='/health/ready'))); assert r.status_code == 200"]

CMD ["python", "-c", "import asyncio, signal; from talent_screener.composition_root import build_production_container; bundle = build_production_container(); stop = asyncio.Event(); loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.add_signal_handler(signal.SIGTERM, stop.set); loop.run_until_complete(stop.wait())"]
