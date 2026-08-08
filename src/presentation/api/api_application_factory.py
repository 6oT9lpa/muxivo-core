from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.contracts.api.api_error_schema import ApiErrorSchema
from src.infrastructure.api.api_settings import ApiSettings
from src.infrastructure.logging import get_logger
from src.presentation.api.action_router import router as action_router
from src.presentation.api.api_composition_root import ApiCompositionRoot
from src.presentation.api.api_container import ApiContainer
from src.presentation.api.api_exception_handlers import register_api_exception_handlers
from src.presentation.api.feedback_router import router as feedback_router
from src.presentation.api.health_router import router as health_router
from src.presentation.api.moderation_router import router as moderation_router
from src.presentation.api.policy_router import router as policy_router
from src.domain.product_identity import PRODUCT_NAME

logger = get_logger(__name__)


def create_api_application(
    database_url: str,
    settings: ApiSettings,
    container: ApiContainer | None = None,
) -> FastAPI:
    if not settings.internal_api_key:
        raise RuntimeError("MUXIVO_CORE_INTERNAL_API_KEY must be configured")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_container = container or ApiCompositionRoot(database_url, settings).build()
        app.state.container = runtime_container
        await runtime_container.moderation_queue.start()
        try:
            await runtime_container.database.initialize()
            runtime_container.database_ready = True
            logger.info("API database initialized")
        except Exception:
            logger.exception("API database initialization failed")
        try:
            policy_version = await runtime_container.service.initialize_policy_status()
            runtime_container.policy_ready = True
            runtime_container.policy_version = policy_version
        except Exception:
            logger.error("API policy initialization failed")
        logger.info("Local moderator API started")
        try:
            yield
        finally:
            await runtime_container.moderation_queue.stop()
            if runtime_container.media_service is not None:
                await runtime_container.media_service.close()
            await runtime_container.database.close()
            logger.info("Local moderator API stopped")

    docs_url = "/docs" if settings.api_docs_enabled else None
    redoc_url = "/redoc" if settings.api_docs_enabled else None
    openapi_url = "/openapi.json" if settings.api_docs_enabled else None
    app = FastAPI(title=PRODUCT_NAME, docs_url=docs_url, redoc_url=redoc_url, openapi_url=openapi_url, lifespan=lifespan)
    register_api_exception_handlers(app)
    _register_security_middleware(app, settings)
    app.include_router(health_router)
    app.include_router(moderation_router)
    app.include_router(feedback_router)
    app.include_router(action_router)
    app.include_router(policy_router)
    return app


def _register_security_middleware(app: FastAPI, settings: ApiSettings) -> None:
    @app.middleware("http")
    async def protect_request(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-Id")
        request.state.correlation_id = correlation_id if _is_safe_correlation_id(correlation_id) else uuid4().hex
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > settings.api_max_body_bytes:
            return _safe_error(request, 413, "payload_too_large", "Request is too large")
        body = await request.body()
        if len(body) > settings.api_max_body_bytes:
            return _safe_error(request, 413, "payload_too_large", "Request is too large")
        client_host = request.client.host if request.client else "unknown"
        key = f"{client_host}:{request.method}:{request.url.path}"
        if not await request.app.state.container.rate_limiter.allow(key):
            return _safe_error(request, 429, "rate_limited", "Too many requests")
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        logger.info(
            "API request completed method=%s endpoint=%s status_code=%s correlation_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            request.state.correlation_id,
        )
        return response


def _is_safe_correlation_id(value: str | None) -> bool:
    return value is not None and 1 <= len(value) <= 64 and all(character.isalnum() or character in "_.-" for character in value)


def _safe_error(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    payload = ApiErrorSchema(code=code, message=message, correlation_id=request.state.correlation_id)
    response = JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
    response.headers["X-Correlation-Id"] = request.state.correlation_id
    return response
