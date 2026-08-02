import logging
import socket
from urllib.parse import urlparse

from backend.config import Settings

logger = logging.getLogger(__name__)


def configure_observability(settings: Settings, app=None) -> None:
    if not settings.phoenix_enabled:
        return
    endpoint = urlparse(settings.phoenix_collector_endpoint)
    host = endpoint.hostname or "127.0.0.1"
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.3):
            pass
    except OSError:
        logger.info(
            "Phoenix collector is not running at %s; tracing is disabled for this session",
            settings.phoenix_collector_endpoint,
        )
        return
    try:
        from phoenix.otel import register

        provider = register(
            project_name="devvy",
            endpoint=settings.phoenix_collector_endpoint,
            batch=True,
            auto_instrument=True,
            verbose=False,
        )
        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        logger.info("Phoenix tracing enabled at %s", settings.phoenix_collector_endpoint)
    except Exception as exc:
        logger.warning("Phoenix unavailable; continuing without tracing: %s", exc)
