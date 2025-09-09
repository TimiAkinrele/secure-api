"""
Production-lean FastAPI service with:
- Liveness & readiness endpoints for Kubernetes probes
- Echo endpoint (simple functional example)
- Prometheus metrics (request counts, latency) with safe labels
- Error handling that still records metrics on failures
- Basic security middleware (host allowlist, gzip)
- Environment-based configuration

Run locally:
  uvicorn app:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time
import logging


# -----------------------------
# Configuration (12-factor)
# -----------------------------
class Settings(BaseSettings):
    APP_NAME: str = "secure-api"
    APP_VERSION: str = "1.0.0"
    # Comma-separated list of hostnames this service will trust (e.g., "api.localdev,localhost")
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"
    # Gate the /metrics endpoint if you ever need to disable it
    METRICS_ENABLED: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()


# -----------------------------
# App & logging setup
# -----------------------------
app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

# Basic, JSON-ish logging. In containers this goes to stdout for aggregation.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(settings.APP_NAME)

# Security/operability middleware:
# - TrustedHostMiddleware blocks requests with unexpected Host headers (helps prevent DNS rebinding).
# - GZipMiddleware reduces payload size for larger JSON responses (cheap perf win).
allowed_hosts = [h.strip() for h in settings.ALLOWED_HOSTS.split(",") if h.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["*"])
app.add_middleware(GZipMiddleware, minimum_size=512)


# -----------------------------
# Prometheus metrics
# -----------------------------
# NOTE on labels: Use stable, LOW-cardinality labels (route template, method, code).
# Avoid putting raw user input or unbounded IDs into labels (explodes time-series).
REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=["route", "method", "code"],
)

LATENCY = Histogram(
    "http_request_latency_seconds",
    "Request latency in seconds",
    labelnames=["route", "method"],
    # Optional: you can tune buckets for your SLOs
    # buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5)
)


def route_label_from_request(request: Request) -> str:
    """
    Derive a SAFE route label:
    - Prefer the route's path template (e.g., '/echo'), not the raw URL (which might include IDs).
    - Fall back to actual path if no route is set (startup race, 404s, etc.).
    """
    try:
        return request.scope.get("route").path  # type: ignore[attr-defined]
    except Exception:
        return request.url.path


# -----------------------------
# Models
# -----------------------------
class Echo(BaseModel):
    """Request model for /echo. Pydantic provides validation & helpful errors."""
    message: str


# -----------------------------
# Health / readiness
# -----------------------------
@app.get("/live", tags=["health"])
def live() -> dict:
    """
    LIVENESS: lightweight check to tell K8s the process is up.
    Should NOT perform external calls; if this fails, restart the container.
    """
    return {"status": "alive", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/ready", tags=["health"])
def ready() -> dict:
    """
    READINESS: indicates the app is ready to receive traffic.
    In a real system, you might check DB connections, caches, etc.
    Keep it fast; make external checks with timeouts if needed.
    """
    return {"status": "ready"}


# -----------------------------
# Functional endpoints
# -----------------------------
@app.get("/health", tags=["health"])
def health() -> dict:
    """Legacy/simple probe used by some platforms and local tests."""
    return {"status": "ok"}


@app.post("/echo", tags=["api"])
def echo(payload: Echo) -> dict:
    """
    Minimal example endpoint: validates JSON body and returns it.
    This is intentionally simple so you can focus on DevSecOps plumbing.
    """
    return {"echo": payload.message}


# -----------------------------
# Metrics middleware
# -----------------------------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """
    Wrap every request to:
    - time the handler
    - increment counters with method/route/status_code
    - record latency histogram
    The middleware ALWAYS records metrics, even if handlers raise errors.
    """
    start = time.perf_counter()
    route_label = route_label_from_request(request)
    method = request.method

    try:
        response = await call_next(request)
        code = response.status_code
        return response

    except Exception as exc:
        # Ensure failures are visible in metrics (code = 500)
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
        log.exception("Unhandled exception while processing request")
        return JSONResponse(
            {"detail": "Internal Server Error"}, status_code=code
        )

    finally:
        # Record metrics in a finally block so we capture both success and error paths
        duration = time.perf_counter() - start
        try:
            LATENCY.labels(route_label, method).observe(duration)
            REQUESTS.labels(route_label, method, str(code)).inc()
        except Exception:
            # Never let metrics crash the request pipeline
            pass


# -----------------------------
# Prometheus scrape endpoint
# -----------------------------
@app.get("/metrics")
def metrics():
    """
    Exposes Prometheus metrics in the standard text format.
    Prometheus will scrape this endpoint (e.g., every 15s).
    You can gate it with METRICS_ENABLED if needed.
    """
    if not settings.METRICS_ENABLED:
        return JSONResponse({"detail": "metrics disabled"}, status_code=404)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# -----------------------------
# Startup / shutdown hooks (optional)
# -----------------------------
@app.on_event("startup")
async def on_startup():
    """
    Place to warm caches, validate config, seed connections, etc.
    Keep it short to avoid slow container starts; use timeouts on external calls.
    """
    log.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)


@app.on_event("shutdown")
async def on_shutdown():
    """Clean up resources if needed (close pools, flush telemetry)."""
    log.info("Shutting down %s", settings.APP_NAME)