"""Structured logging and Prometheus metrics."""

import json
import logging
import uuid

import pytest

from app.config import get_settings
from app.core.logging_config import JsonFormatter
from app.core.metrics import REGISTRY
from app.core.middleware import RequestIdLogFilter, request_id_var
from app.db.audit import record_security_event


def _sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- logging ------------------------------------------------------------------------------------


def test_json_formatter_writes_one_object_with_request_id_and_extras():
    token = request_id_var.set("req-12345678")
    try:
        record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
        record.http_status = 418
        RequestIdLogFilter().filter(record)
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)
    entry = json.loads(line)
    assert "\n" not in line
    assert entry["msg"] == "hello world" and entry["level"] == "WARNING" and entry["logger"] == "app.test"
    assert entry["request_id"] == "req-12345678" and entry["http_status"] == 418
    assert entry["ts"].endswith("+00:00")


def test_json_formatter_includes_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    entry = json.loads(JsonFormatter().format(record))
    assert entry["exc_type"] == "ValueError" and "boom" in entry["exc"]


def test_access_log_has_request_id_and_no_query_string(client, caplog):
    caplog.set_level(logging.INFO)
    resp = client.get("/health?token=hunter2-secret", headers={"X-Request-ID": "trace-abcdef12"})
    records = [r for r in caplog.records if r.name == "app.access"]
    assert len(records) == 1
    record = records[0]
    assert resp.headers["X-Request-ID"] == "trace-abcdef12" == record.request_id
    assert record.http_status == 200 and record.http_route == "/health" and record.duration_ms >= 0
    formatted = JsonFormatter().format(record)
    assert "hunter2" not in formatted and "hunter2" not in record.getMessage()


# --- metrics ------------------------------------------------------------------------------------


def test_metrics_endpoint_exposes_request_counters(client):
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert 'sentinel_http_requests_total{method="GET",route="/health",status="2xx"}' in resp.text


def test_routes_are_labelled_by_template_not_by_id(client):
    fetch_id = str(uuid.uuid4())
    before = _sample("sentinel_http_requests_total", {"method": "GET", "route": "/documents/{document_id}/chunks", "status": "4xx"})
    client.get(f"/documents/{fetch_id}/chunks")  # 401: not signed in
    client.get("/no-such-page-" + fetch_id)
    after = _sample("sentinel_http_requests_total", {"method": "GET", "route": "/documents/{document_id}/chunks", "status": "4xx"})
    assert after == before + 1
    text = client.get("/metrics").text
    assert fetch_id not in text
    assert 'route="unmatched"' in text


@pytest.mark.parametrize(
    "env,token,enabled,header,expected",
    [
        ("test", "", True, None, 200),                       # no token configured: open outside production
        ("production", "", True, None, 404),                 # production without a token: disabled
        ("production", "s3cret-metrics-token", True, None, 404),
        ("production", "s3cret-metrics-token", True, "Bearer wrong", 404),
        ("production", "s3cret-metrics-token", True, "Bearer s3cret-metrics-token", 200),
        ("test", "", False, None, 404),                      # switched off
    ],
)
def test_metrics_access_control(client, monkeypatch, env, token, enabled, header, expected):
    settings = get_settings()
    monkeypatch.setattr(settings, "APP_ENV", env)
    monkeypatch.setattr(settings, "METRICS_TOKEN", token)
    monkeypatch.setattr(settings, "METRICS_ENABLED", enabled)
    resp = client.get("/metrics", headers={"Authorization": header} if header else {})
    assert resp.status_code == expected
    if expected == 404:
        assert "sentinel_" not in resp.text


def test_security_events_are_counted(session_factory):
    labels = {"event_type": "input_blocked", "severity": "high"}
    before = _sample("sentinel_security_events_total", labels)
    record_security_event(session_factory, "input_blocked", "input_guard", severity="high")
    assert _sample("sentinel_security_events_total", labels) == before + 1


def test_credit_spends_are_counted(app, client_factory):
    from helpers import signup

    user = signup(client_factory())
    before = _sample("sentinel_credits_spent_total", {"reason": "query"})
    app.state.credit_service.spend(user["user_id"], 2, "query")
    assert _sample("sentinel_credits_spent_total", {"reason": "query"}) == before + 2
