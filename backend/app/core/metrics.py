"""Prometheus metrics, served at GET /metrics for a scraper on the private network.

Labels are bounded on purpose: routes are recorded by their template (`/documents/{document_id}`,
never the concrete ID) and unmatched paths collapse into one label, so a scanner cannot
blow up the series count. No user, workspace or question data is ever a label."""

import hmac

from fastapi import Depends, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

from app.config import Settings, get_settings
from app.core.exceptions import AppError

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "sentinel_http_requests_total", "HTTP requests by route template and status class.",
    ["method", "route", "status"], registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "sentinel_http_request_duration_seconds", "HTTP request latency.", ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30), registry=REGISTRY,
)
SECURITY_EVENTS = Counter(
    "sentinel_security_events_total", "Security events recorded, by type and severity.",
    ["event_type", "severity"], registry=REGISTRY,
)
QUERIES = Counter(
    "sentinel_queries_total", "Chat queries by outcome.", ["outcome"], registry=REGISTRY,
)
QUERY_STAGE_LATENCY = Histogram(
    "sentinel_query_stage_seconds", "Chat query latency by pipeline stage.", ["stage"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 60), registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "sentinel_llm_tokens_total", "LLM tokens used, by kind (prompt / completion).", ["kind"], registry=REGISTRY,
)
CREDITS_SPENT = Counter(
    "sentinel_credits_spent_total", "Credits charged, by reason (before refunds).", ["reason"], registry=REGISTRY,
)


def route_label(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def observe_request(request: Request, status_code: int, seconds: float) -> None:
    route = route_label(request)
    HTTP_REQUESTS.labels(request.method, route, f"{status_code // 100}xx").inc()
    HTTP_LATENCY.labels(request.method, route).observe(seconds)


def observe_query(*, blocked: bool, timings, usage: dict | None) -> None:
    QUERIES.labels("blocked" if blocked else "answered").inc()
    for stage in ("security_ms", "retrieval_ms", "generation_ms", "total_ms"):
        value = getattr(timings, stage, None)
        if value is not None:
            QUERY_STAGE_LATENCY.labels(stage.removesuffix("_ms")).observe(value / 1000)
    usage = usage or {}
    for kind, keys in (("prompt", ("prompt_tokens", "prompt_eval_count")), ("completion", ("completion_tokens", "eval_count"))):
        count = next((usage[k] for k in keys if usage.get(k)), 0)
        if count:
            LLM_TOKENS.labels(kind).inc(count)


class MetricsUnavailableError(AppError):
    status_code = 404
    error_type = "not_found"


def require_metrics_token(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """/metrics needs `Authorization: Bearer <METRICS_TOKEN>`. Without a configured token it
    is open outside production (local Prometheus) and disabled in production."""
    token = settings.METRICS_TOKEN
    if not settings.METRICS_ENABLED or (not token and settings.APP_ENV == "production"):
        raise MetricsUnavailableError("Not found.")
    if token:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise MetricsUnavailableError("Not found.")


async def metrics_endpoint(_: None = Depends(require_metrics_token)) -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

