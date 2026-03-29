import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.settings import settings
from app.telemetry import setup_telemetry

# IMPORTANT: telemetry must be initialized before the FastAPI app is created,
# because FastAPIInstrumentor patches the ASGI middleware at app creation time.
setup_telemetry()

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(settings.app_name, settings.app_version)
meter = metrics.get_meter(settings.app_name, version=settings.app_version)

request_counter = meter.create_counter(
    name="api.requests.total",
    description="Total number of API requests handled",
    unit="1",
)
request_duration = meter.create_histogram(
    name="api.request.duration",
    description="Duration of API requests in milliseconds",
    unit="ms",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting %s v%s [%s]", settings.app_name, settings.app_version, settings.environment)
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

FastAPIInstrumentor.instrument_app(app)


@app.get("/")
async def root() -> dict[str, str]:
    start = time.monotonic()

    with tracer.start_as_current_span("root-handler") as span:
        span.set_attribute("handler.name", "root")
        span.set_attribute("app.version", settings.app_version)

        logger.info("Handling GET /")

        result = {
            "message": "Hello from FastAPI with OpenTelemetry",
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
        }

    duration_ms = (time.monotonic() - start) * 1000
    request_counter.add(1, {"endpoint": "/", "method": "GET", "status": "200"})
    request_duration.record(duration_ms, {"endpoint": "/"})

    return result


@app.get("/health")
async def health() -> dict[str, str]:
    """Lightweight liveness probe — minimal OTel overhead."""
    return {"status": "ok"}
