import asyncio

from aiohttp import (
    ClientConnectionError,
    ClientPayloadError,
    ClientSession,
    TraceConfig,
)
from aiohttp_retry import ExponentialRetry, RetryClient

from .asyncio import RateLimiterMixin
from .logging import get_logger

logger = get_logger()


async def on_request_end(session, context, params):
    if params.response.ok:
        return

    logger.error(
        (
            f"{params.response.status} {params.response.reason}: "
            f"{params.method} {params.response.url}"
        ),
    )


async def on_request_exception(session, context, params):
    if isinstance(params.exception, asyncio.CancelledError):
        return

    logger.error(f"{params.exception}: {params.method} {params.url}")


trace_config = TraceConfig()
trace_config.on_request_end.append(on_request_end)
trace_config.on_request_exception.append(on_request_exception)


class RateLimitRetry(ExponentialRetry):
    def __init__(self, *args, rate_limit_timeout, **kwargs):
        super().__init__(*args, **kwargs)

        self.rate_limit_timeout = rate_limit_timeout

    def get_timeout(self, attempt, response, *args, **kwargs):
        if response is not None and response.status in {408, 429}:
            self.attempts += 1
            return self.rate_limit_timeout

        return super().get_timeout(attempt, response, *args, **kwargs)


class RetryClientSession(RetryClient):
    def __init__(
        self,
        base_url=None,
        *args,
        connection_limit=None,
        raise_for_status=True,
        **kwargs,
    ):
        trace_configs = kwargs.pop("trace_configs", [trace_config])

        super().__init__(
            *args,
            **kwargs,
            base_url=base_url,
            raise_for_status=raise_for_status,
            trace_configs=trace_configs,
        )

        if connection_limit is not None:
            self._client._connector._limit = connection_limit


class RateLimitedRetryClientSession(RateLimiterMixin, RetryClientSession):
    def __init__(
        self,
        *args,
        attempts=3,
        connection_limit=None,
        rate_limit,
        rate_limit_period=60,
        start_timeout=0.25,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
            client_session=ClientSession(*args, **kwargs),
            connection_limit=connection_limit,
            rate_limit=rate_limit,
            rate_limit_period=rate_limit_period,
            retry_options=RateLimitRetry(
                attempts=attempts,
                evaluate_response_callback=self.response_callback,
                exceptions={
                    ClientConnectionError,
                    ClientPayloadError,
                    asyncio.TimeoutError,
                },
                rate_limit_timeout=rate_limit_period,
                start_timeout=start_timeout,
            ),
        )

        self._client_request = self._client._request
        self._client._request = self._request

    async def _request(self, *args, **kwargs):
        async with self._rate_limiter:
            return await self._client_request(*args, **kwargs)

    async def response_callback(self, response):
        return response is not None and response.status not in {408, 429}
