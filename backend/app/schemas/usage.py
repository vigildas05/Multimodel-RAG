from pydantic import BaseModel


class UsageResponse(BaseModel):
    queries_today: int
    daily_query_limit: int
    is_demo: bool
    # ISO timestamp when a demo workspace (and its session) expires; null for real accounts.
    workspace_expires_at: str | None
