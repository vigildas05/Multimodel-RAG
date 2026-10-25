from dataclasses import dataclass


@dataclass(frozen=True)
class TenantScope:
    """The workspace a request is allowed to touch.

    Only ever constructed from a verified access token (see app.auth.dependencies) — never
    from request bodies, query parameters or headers, so a client cannot widen its own scope.
    """

    workspace_id: str
    user_id: str
