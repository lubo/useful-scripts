import asyncio
from http import HTTPStatus

from aiohttp import (
    ClientConnectionError,
    ClientPayloadError,
    ClientSession,
    TCPConnector,
    TraceConfig,
)
from aiohttp_retry import ExponentialRetry, RetryClient
from overrides import override

from .asyncio import RateLimiterMixin
from .logging import get_logger

logger = get_logger()

RATE_LIMIT_STATUS_CODES = {
    HTTPStatus.REQUEST_TIMEOUT.value,
    HTTPStatus.TOO_MANY_REQUESTS.value,
}


async def on_request_end(
    session,  # noqa: ARG001
    context,  # noqa: ARG001
    params,
):
    if params.response.ok:
        return

    logger.error(
        "%d %s: %s %s",
        params.response.status,
        params.response.reason,
        params.method,
        params.response.url,
    )


async def on_request_exception(
    session,  # noqa: ARG001
    context,  # noqa: ARG001
    params,
):
    if isinstance(params.exception, asyncio.CancelledError):
        return

    logger.error(
        "%s: %s %s",
        params.exception,
        params.method,
        params.url,
    )


trace_config = TraceConfig()
trace_config.on_request_end.append(on_request_end)
trace_config.on_request_exception.append(on_request_exception)


class RateLimitedClientSession(RateLimiterMixin, ClientSession):
    @override
    async def _request(self, *args, **kwargs):
        async with self._RateLimiterMixin_rate_limiter:
            return await super()._request(*args, **kwargs)

    @override
    async def close(self) -> None:
        await super().close()

        self._RateLimiterMixin_rate_limiter.close()


class RateLimitRetry(ExponentialRetry):
    def __init__(self, *args, rate_limit_timeout, **kwargs):
        super().__init__(*args, **kwargs)

        self.__rate_limit_timeout = rate_limit_timeout

    def get_timeout(self, attempt, response, *args, **kwargs):
        if response is not None and response.status in RATE_LIMIT_STATUS_CODES:
            self.attempts += 1
            return self.__rate_limit_timeout

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
            connector=(
                None
                if connection_limit is None
                else TCPConnector(
                    limit=connection_limit,
                )
            ),
            raise_for_status=raise_for_status,
            trace_configs=trace_configs,
        )


class RateLimitedRetryClientSession(RetryClientSession):
    def __init__(
        self,
        *args,
        attempts=3,
        connection_limit=None,
        rate_limit_period=60,
        start_timeout=0.25,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
            client_session=RateLimitedClientSession(
                *args,
                **kwargs,
                rate_limit_period=rate_limit_period,
            ),
            connection_limit=connection_limit,
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

    async def response_callback(self, response):
        return (
            response is not None
            and response.status not in RATE_LIMIT_STATUS_CODES
        )
