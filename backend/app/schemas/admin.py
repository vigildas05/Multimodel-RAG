from typing import Any

from pydantic import BaseModel


class SecurityEventOut(BaseModel):
    id: int
    created_at: str
    event_type: str
    source: str
    severity: str
    risk_score: float | None
    workspace_id: str | None
    user_id: str | None
    user_email: str | None
    request_id: str | None
    details: dict[str, Any]


class SecurityEventPage(BaseModel):
    total: int
    limit: int
    offset: int
    events: list[SecurityEventOut]


class RedteamReportList(BaseModel):
    reports: list[str]
