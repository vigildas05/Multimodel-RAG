"""Redis-backed rate limiting (works with local Redis or Upstash via REDIS_URL).

Sliding-window log: each subject (an IP address, a user ID, an email fingerprint) gets a
sorted set of request timestamps; one atomic Lua script trims old entries, checks the
count and records the new request. Unlike fixed windows there's no burst at window
boundaries, and the clock is Redis's own (TIME), so app servers can't disagree about it.
Only allowed requests are recorded, so hammering a blocked endpoint doesn't extend the block.

If Redis is unreachable the limiter fails OPEN (requests proceed, a warning is logged):
availability is preferred over strictness here, and quotas/limits elsewhere still apply.
"""

import logging
import math
import secrets
from dataclasses import dataclass

import redis
from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import RateLimitedError

logger = logging.getLogger("app.core.rate_limit")

_SLIDING_WINDOW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
if redis.call('ZCARD', KEYS[1]) >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, tonumber(oldest[2]) + window - now}
end
redis.call('ZADD', KEYS[1], now, now .. '-' .. ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}
"""


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter:
    def __init__(self, redis_url: str, enabled: bool = True, key_prefix: str = "rl"):
        self.enabled = enabled
        self._prefix = key_prefix
        self._client = redis.Redis.from_url(redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._script = self._client.register_script(_SLIDING_WINDOW)

    def hit(self, policy: RateLimitPolicy, subject: str) -> RateLimitResult:
        if not self.enabled:
            return RateLimitResult(allowed=True)
        key = f"{self._prefix}:{policy.name}:{subject}"
        try:
            allowed, retry_ms = self._script(
                keys=[key], args=[policy.window_seconds * 1000, policy.limit, secrets.token_hex(4)]
            )
        except redis.RedisError as exc:
            logger.warning("Rate limiter unavailable, allowing request (%s): %s", policy.name, exc)
            return RateLimitResult(allowed=True)
        if allowed:
            return RateLimitResult(allowed=True)
        return RateLimitResult(allowed=False, retry_after_seconds=max(1, math.ceil(int(retry_ms) / 1000)))

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except redis.RedisError:
            return False

    def check(self, policy: RateLimitPolicy, subject: str) -> None:
        """Raise RateLimitedError (429 + Retry-After) if `subject` is over `policy`."""
        result = self.hit(policy, subject)
        if not result.allowed:
            raise RateLimitedError(
                "Too many requests. Please slow down and try again shortly.",
                retry_after=result.retry_after_seconds,
            )


def client_ip(request: Request) -> str:
    # Behind the reverse proxy, uvicorn's --proxy-headers / --forwarded-allow-ips makes
    # request.client the real client (only trusting the proxy's X-Forwarded-For).
    return request.client.host if request.client else "unknown"


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def limit_by_ip(policy_name: str):
    """Route dependency: limit by client IP (for unauthenticated routes)."""

    async def dependency(request: Request, limiter: RateLimiter = Depends(get_rate_limiter)) -> None:
        policy = request.app.state.rate_limit_policies[policy_name]
        await run_in_threadpool(limiter.check, policy, client_ip(request))

    return dependency


def limit_by_user(policy_name: str):
    """Route dependency: limit by authenticated user (for expensive authenticated routes)."""
    from app.auth.dependencies import get_current_user  # avoid an import cycle at module load

    async def dependency(
        request: Request,
        user=Depends(get_current_user),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> None:
        policy = request.app.state.rate_limit_policies[policy_name]
        await run_in_threadpool(limiter.check, policy, user.user_id)

    return dependency


def build_policies(settings) -> dict[str, RateLimitPolicy]:
    def p(name: str, limit: int, window: int) -> tuple[str, RateLimitPolicy]:
        return name, RateLimitPolicy(name, limit, window)

    return dict(
        [
            p("global_ip", settings.RATE_LIMIT_GLOBAL_PER_IP_PER_MINUTE, 60),
            p("login_ip", settings.RATE_LIMIT_LOGIN_PER_IP_PER_MINUTE, 60),
            p("login_account", settings.RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_HOUR, 3600),
            p("register_ip", settings.RATE_LIMIT_REGISTER_PER_IP_PER_HOUR, 3600),
            p("demo_ip", settings.RATE_LIMIT_DEMO_PER_IP_PER_MINUTE, 60),
            p("refresh_ip", settings.RATE_LIMIT_REFRESH_PER_IP_PER_MINUTE, 60),
            p("chat_user", settings.RATE_LIMIT_CHAT_PER_USER_PER_MINUTE, 60),
            p("search_user", settings.RATE_LIMIT_SEARCH_PER_USER_PER_MINUTE, 60),
            p("upload_user", settings.RATE_LIMIT_UPLOAD_PER_USER_PER_MINUTE, 60),
            p("billing_user", settings.RATE_LIMIT_BILLING_PER_USER_PER_MINUTE, 60),
        ]
    )
