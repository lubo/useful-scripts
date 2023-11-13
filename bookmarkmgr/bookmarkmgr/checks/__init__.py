from contextlib import AbstractAsyncContextManager
from urllib.parse import urlparse

from ..curl import RateLimitedRetryCurlSession, RetryCurlSession


class CheckSessionManager(AbstractAsyncContextManager):
    def __init__(self, host_rate_limits):
        self._sessions = {
            hostname.lower(): RateLimitedRetryCurlSession(
                rate_limit=limit,
                rate_limit_period=period,
            )
            for hostname, limit, period in host_rate_limits
        }
        self._sessions[None] = RetryCurlSession(
            max_clients=100,
        )

    async def __aexit__(self, *args, **kwargs):
        for session in self._sessions.values():
            session.close()

    def get_session(self, url):
        return self._sessions.get(
            urlparse(url).hostname.lower(),
            self._sessions[None],
        )
