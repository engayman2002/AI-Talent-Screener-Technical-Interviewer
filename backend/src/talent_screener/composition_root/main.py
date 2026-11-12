"""Runnable Backend Runtime CLI Entrypoint (`python -m talent_screener.composition_root.main`).

Loads `.env` configuration (zero external dependencies), assembles the Clean Architecture
container, seeds the idempotent interactive Acme AI demo environment when enabled, and starts
either the built-in zero-dependency `AsyncStdlibHttpWsServer` or exposes an ASGI `app`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from talent_screener.composition_root.container import (
    Phase8PipelineBundle,
    build_runtime_container_from_env,
)
from talent_screener.composition_root.demo_seeder import seed_interactive_demo_environment
from talent_screener.presentation.server import AsyncStdlibHttpWsServer

logger = logging.getLogger(__name__)


def load_dotenv_into_environ(search_paths: tuple[Path, ...] | None = None) -> Path | None:
    """Parse KEY=VALUE pairs from `.env` if present without overriding real shell env vars."""
    candidates = search_paths or (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[4] / ".env",
    )
    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        return path
    return None


def create_runtime_asgi_bundle() -> Phase8PipelineBundle:
    """Build runtime container after loading `.env` defaults."""
    load_dotenv_into_environ()
    return build_runtime_container_from_env()


async def run_server_async(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    seed_only: bool = False,
) -> None:
    """Boot container, seed interactive demo workspace if enabled, and serve HTTP/SSE/WS."""
    load_dotenv_into_environ()
    bundle = build_runtime_container_from_env()
    demo_enabled = os.environ.get("DEMO_SEED_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    if demo_enabled:
        manifest = await seed_interactive_demo_environment(bundle)
        print(
            "\n"
            "========================================================================\n"
            " AI Talent Screener & Technical Interviewer — Live Runtime Ready\n"
            "========================================================================\n"
            f"  Tenant          : {manifest.organization_name} ({manifest.tenant_id})\n"
            f"  Requisition ID  : {manifest.requisition_id} ({manifest.requisition_title})\n"
            f"  Demo Batch ID   : {manifest.batch_id}\n"
            f"  Active Session  : {manifest.active_session_id} (Turn 1 IN_PROGRESS)\n"
            f"  Report IDs      : {len(manifest.report_ids)} finalized reports\n"
            f"  LLM Runtime     : {manifest.llm_mode.upper()} "
            f"({manifest.llm_provider} / {manifest.llm_model})\n"
            f"  API Base URL    : http://{host}:{port}\n"
            f"  Swagger Docs    : http://{host}:{port}/docs\n"
            f"  Demo Manifest   : http://{host}:{port}/v1/ops/demo-manifest\n"
            "========================================================================\n"
        )
    else:
        mode, provider, model = bundle.llm_runtime_metadata()
        print(
            f"AI Talent Screener API starting on http://{host}:{port} "
            f"[LLM={mode}:{provider}/{model}]"
        )

    if seed_only:
        bundle.db_manager.close()
        return

    asgi_app = bundle.create_asgi_app()
    server = AsyncStdlibHttpWsServer(asgi_app, host=host, port=port)
    try:
        await server.serve_forever()
    finally:
        await server.stop()
        bundle.db_manager.close()


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint for launching the backend API server or seeding demo state."""
    parser = argparse.ArgumentParser(
        description="Run AI Talent Screener & Technical Interviewer ASGI/HTTP/WebSocket server."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("API_HOST", "127.0.0.1"),
        help="Bind host interface (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("API_PORT", "8000")),
        help="Bind TCP port (default: 8000)",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Seed interactive demo environment into DB_DSN and exit cleanly.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(
            run_server_async(
                host=args.host,
                port=args.port,
                seed_only=args.seed_only,
            )
        )
    except KeyboardInterrupt:
        logger.info("Server shutdown requested via KeyboardInterrupt.")


if __name__ == "__main__":
    main()
