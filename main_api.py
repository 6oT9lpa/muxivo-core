"""Canonical production entry point for the Muxivo Core HTTP service."""

import asyncio
import sys

import uvicorn

from src.infrastructure.api.api_settings import ApiSettings
from src.infrastructure.config.loader import get_config
from src.infrastructure.logging import get_logger
from src.presentation.api.api_application_factory import create_api_application

logger = get_logger(__name__)

# psycopg async I/O requires a selector loop on Windows. Configure it before
# Uvicorn creates the application loop; Linux deployments are unaffected.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

settings = ApiSettings()

if settings.api_host not in {"127.0.0.1", "::1", "localhost"}:
    logger.warning("API is configured for a non-loopback host")

app = create_api_application(get_config().database_url, settings)


def _selector_loop_factory():
    return asyncio.SelectorEventLoop()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=False,
        loop=_selector_loop_factory if sys.platform == "win32" else "auto",
    )
