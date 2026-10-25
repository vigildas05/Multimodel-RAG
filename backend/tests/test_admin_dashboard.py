"""Admin dashboard APIs: admin-only, filters work, usage aggregates are right, and the
red-team report reader cannot be pointed outside its directory."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from helpers import signup
from sqlalchemy import update

from app.db.audit import add_security_event
from app.db.models import QueryLog, SecurityEvent, User
from app.services.admin_service import AdminService


@pytest.fixture
def admin(client_factory, session_factory):
    client = client_factory()
    user = signup(client)
    with session_factory.begin() as session:
        session.execute(update(User).where(User.id == user["user_id"]).values(role="admin"))
    return client, user


@pytest.fixture
def regular(client_factory):
    client = client_factory()
    return client, signup(client)


ADMIN_GETS = [
    "/admin/security-events",
    "/admin/security-summary",
    "/admin/usage",
    "/admin/redteam/reports",
    "/admin/redteam/latest",
]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_non_admins_get_403(regular, path):
    client, _ = regular
    assert client.get(path).status_code == 403


def _seed_events(session_factory, user, other):
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        add_security_event(session, "input_blocked", "input_guard", severity="high", risk_score=0.93,
                           user_id=user["user_id"], workspace_id=user["workspace_id"], details={"question": "<b>x</b>"})
        add_security_event(session, "input_flagged", "input_guard", severity="low", risk_score=0.55,
                           user_id=user["user_id"], workspace_id=user["workspace_id"])
        add_security_event(session, "chunk_quarantined", "ingest_guard", severity="medium",
                           user_id=other["user_id"], workspace_id=other["workspace_id"])
        add_security_event(session, "auth_failure", "auth", severity="low")
        session.flush()
        # One old event, outside a "since yesterday" window.
        old = SecurityEvent(event_type="prompt_leak", source="output_guard", severity="critical",
                            created_at=now - timedelta(days=30), details={})
        session.add(old)


def test_security_events_filters(admin, regular, session_factory):
    client, me = admin
    _, other = regular
    _seed_events(session_factory, me, other)

    everything = client.get("/admin/security-events").json()
    assert everything["total"] == 5
    times = [e["created_at"] for e in everything["events"]]
    assert times == sorted(times, reverse=True)

    def types(**params):
        body = client.get("/admin/security-events", params=params).json()
        return sorted(e["event_type"] for e in body["events"])

    assert types(source="input_guard") == ["input_blocked", "input_flagged"]
    assert types(severity="critical") == ["prompt_leak"]
    assert types(event_type="auth_failure") == ["auth_failure"]
    assert types(workspace_id=other["workspace_id"]) == ["chunk_quarantined"]
    assert types(user_id=me["user_id"], severity="high") == ["input_blocked"]
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert "prompt_leak" not in types(since=since)
    assert types(until=since) == ["prompt_leak"]

    blocked = client.get("/admin/security-events", params={"event_type": "input_blocked"}).json()["events"][0]
    assert blocked["user_email"] == me["email"] and blocked["risk_score"] == pytest.approx(0.93)
    # Details are returned as data, untouched; rendering them safely is the client's job.
    assert blocked["details"]["question"] == "<b>x</b>"


def test_security_events_pagination_and_validation(admin, regular, session_factory):
    client, me = admin
    _, other = regular
    _seed_events(session_factory, me, other)
    first = client.get("/admin/security-events", params={"limit": 2}).json()
    second = client.get("/admin/security-events", params={"limit": 2, "offset": 2}).json()
    assert first["total"] == second["total"] == 5
    assert len(first["events"]) == len(second["events"]) == 2
    assert {e["id"] for e in first["events"]}.isdisjoint(e["id"] for e in second["events"])

    for bad in ({"severity": "urgent"}, {"limit": 1000}, {"user_id": "not-a-uuid"}, {"since": "yesterday"}):
        assert client.get("/admin/security-events", params=bad).status_code == 422, bad


def test_security_summary(admin, regular, session_factory):
    client, me = admin
    _, other = regular
    _seed_events(session_factory, me, other)
    summary = client.get("/admin/security-summary", params={"days": 7}).json()
    assert summary["total"] == 4  # the 30-day-old event is outside the window
    assert summary["by_severity"] == {"high": 1, "low": 2, "medium": 1}
    assert len(summary["by_day"]) == 7
    assert sum(sum(d["counts"].values()) for d in summary["by_day"]) == 4
    assert "prompt_leak" in summary["event_types"]  # dropdown values cover all time
    assert {"event_type": "input_blocked", "source": "input_guard", "count": 1} in summary["by_type"]


def test_usage_aggregates(admin, app, session_factory):
    client, me = admin
    with session_factory.begin() as session:
        for total_ms, blocked in ((100, False), (200, False), (300, False), (50, True)):
            session.add(QueryLog(
                workspace_id=me["workspace_id"], user_id=me["user_id"], question_chars=10, question_sha256="0" * 64,
                blocked=blocked, total_ms=total_ms, retrieval_ms=10, prompt_tokens=None if blocked else 100,
                completion_tokens=None if blocked else 20, llm_provider="mock", llm_model="m1",
            ))
    credits = app.state.credit_service
    credits.spend(me["user_id"], 2, "query")
    credits.refund(credits.spend(me["user_id"], 5, "audio_processing"))  # refunded: not counted as spent
    credits.spend(me["user_id"], 1, "query")

    usage = client.get("/admin/usage", params={"days": 7}).json()
    assert usage["totals"]["queries"] == 4
    assert usage["totals"]["blocked"] == 1
    assert usage["totals"]["active_users"] == 1
    assert usage["totals"]["prompt_tokens"] == 300 and usage["totals"]["completion_tokens"] == 60
    assert usage["totals"]["credits_spent"] == 3
    assert usage["latency_ms"]["mean"] == pytest.approx(162.5)
    assert 280 <= usage["latency_ms"]["p95"] <= 300
    today = usage["by_day"][-1]
    assert len(usage["by_day"]) == 7 and today["queries"] == 4 and today["credits_spent"] == 3
    assert usage["top_users"] == [{"email": me["email"], "queries": 4, "blocked": 1}]
    assert {"kind": "spend", "reason": "query", "amount": -3} in usage["credits_by_reason"]
    assert {"kind": "refund", "reason": "refund", "amount": 5} in usage["credits_by_reason"]


def test_usage_validation(admin):
    client, _ = admin
    assert client.get("/admin/usage", params={"days": 0}).status_code == 422
    assert client.get("/admin/usage", params={"days": 365}).status_code == 422


# --- red-team reports ----------------------------------------------------------------------------


@pytest.fixture
def reports_dir(app, tmp_path, monkeypatch):
    report = {
        "generated_at": "2026-10-25T00:00:00+00:00",
        "attacks": {"on": {"overall": {"asr": 0.25}}},
        "records": {"on": [
            {"id": "a1", "category": "obfuscation", "split": "dev", "vector": "query", "goal": "g", "success": True,
             "output_excerpt": "secret"},
            {"id": "a2", "category": "obfuscation", "split": "dev", "vector": "query", "goal": "g", "success": False},
        ]},
    }
    (tmp_path / "full.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "Bad Name.json").write_text("{}", encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path.parent / "outside.json").write_text('{"leak": true}', encoding="utf-8")
    monkeypatch.setattr(app.state.admin_service, "_reports_dir", tmp_path)
    return tmp_path


def test_redteam_report_summary(admin, reports_dir):
    client, _ = admin
    assert client.get("/admin/redteam/reports").json()["reports"] == ["broken", "full"]
    body = client.get("/admin/redteam/latest").json()
    assert body["name"] == "full" and body["attacks"]["on"]["overall"]["asr"] == 0.25
    assert "records" not in body
    # Misses keep identifiers only, not model output.
    assert body["misses"] == [{"id": "a1", "category": "obfuscation", "split": "dev", "vector": "query", "goal": "g"}]


@pytest.mark.parametrize("name", ["../outside", "..%2Foutside", "Bad Name", "missing", "broken", "full.json"])
def test_redteam_report_name_is_confined(admin, reports_dir, name):
    client, _ = admin
    resp = client.get("/admin/redteam/latest", params={"name": name})
    assert resp.status_code in (404, 422)
    assert "leak" not in resp.text


def test_redteam_reports_missing_dir(admin, app, tmp_path, monkeypatch):
    client, _ = admin
    monkeypatch.setattr(app.state.admin_service, "_reports_dir", tmp_path / "nope")
    assert client.get("/admin/redteam/reports").json()["reports"] == []
    assert client.get("/admin/redteam/latest").status_code == 404


def test_committed_report_is_readable(admin):
    # The default directory points at the repo's committed reports.
    client, _ = admin
    body = client.get("/admin/redteam/latest").json()
    assert body["name"] == "full" and body["dataset"]["attacks"] >= 300


@pytest.mark.parametrize("name", ["../outside", r"..\outside", "/etc/passwd", "full.json", "Bad Name"])
def test_report_reader_confines_names_itself(session_factory, reports_dir, name):
    # Defense in depth: the service refuses bad names even without the route's validation.
    assert AdminService(session_factory, reports_dir).redteam_report(name) is None
