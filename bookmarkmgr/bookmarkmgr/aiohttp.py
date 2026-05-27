import asyncio
from http import HTTPStatus
from typing import (
    Any,
    override,
    TYPE_CHECKING,
    TypedDict,
    Unpack,
)

import aiohttp
from aiohttp import (
    BaseConnector,
    ClientConnectionError,
    ClientMiddlewareType,
    ClientPayloadError,
    ClientRequest,
    ClientResponse,
    ClientTimeout,
    ClientWebSocketResponse,
    HttpVersion,
    TCPConnector,
    TraceConfig,
    TraceRequestEndParams,
    TraceRequestExceptionParams,
)
from aiohttp_retry import (
    EvaluateResponseCallbackType,
    ExponentialRetry,
    RetryClient,
)

from .logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence
    from types import SimpleNamespace

    from aiohttp.abc import AbstractCookieJar
    from aiohttp.client import (
        _CharsetResolver,
        _RequestContextManager,
        _RequestOptions,
    )
    from aiohttp.helpers import _SENTINEL, BasicAuth
    from aiohttp.typedefs import (
        JSONEncoder,
        LooseCookies,
        LooseHeaders,
        StrOrURL,
    )

    from .asyncio import RateLimiter

logger = get_logger()

RATE_LIMIT_STATUS_CODES = {
    HTTPStatus.REQUEST_TIMEOUT.value,
    HTTPStatus.TOO_MANY_REQUESTS.value,
    520,  # Web server returns an unknown error (Cloudflare)
}


async def on_request_end(
    session: aiohttp.ClientSession,  # noqa: ARG001
    context: SimpleNamespace,  # noqa: ARG001
    params: TraceRequestEndParams,
) -> None:
    if params.response.ok:
        return

    logger.debug(
        "%d %s: %s %s",
        params.response.status,
        params.response.reason,
        params.method,
        params.response.url,
    )


async def on_request_exception(
    session: aiohttp.ClientSession,  # noqa: ARG001
    context: SimpleNamespace,  # noqa: ARG001
    params: TraceRequestExceptionParams,
) -> None:
    if isinstance(params.exception, asyncio.CancelledError):
        return

    logger.debug(
        "%s: %s %s",
        params.exception,
        params.method,
        params.url,
    )


trace_config = TraceConfig()
trace_config.on_request_end.append(on_request_end)
trace_config.on_request_exception.append(on_request_exception)


class _ClientSessionOptions(TypedDict, total=False):
    base_url: StrOrURL
    connector: BaseConnector
    loop: asyncio.AbstractEventLoop
    cookies: LooseCookies
    headers: LooseHeaders
    proxy: StrOrURL
    proxy_auth: BasicAuth
    skip_auto_headers: Iterable[str]
    auth: BasicAuth
    json_serialize: JSONEncoder
    request_class: type[ClientRequest]
    response_class: type[ClientResponse]
    ws_response_class: type[ClientWebSocketResponse]
    version: HttpVersion
    cookie_jar: AbstractCookieJar
    connector_owner: bool
    raise_for_status: bool | Callable[[ClientResponse], Awaitable[None]]
    read_timeout: float | _SENTINEL
    conn_timeout: float
    timeout: object | ClientTimeout
    auto_decompress: bool
    trust_env: bool
    requote_redirect_url: bool
    trace_configs: list[TraceConfig]
    read_bufsize: int
    max_line_size: int
    max_field_size: int
    max_headers: int
    fallback_charset_resolver: _CharsetResolver
    middlewares: Sequence[ClientMiddlewareType]
    ssl_shutdown_timeout: _SENTINEL | float | None


class ClientSession(aiohttp.ClientSession):
    """Disables redirects by default to prevent protocol downgrades, etc."""

    @override
    async def _request(
        self,
        *args: Any,
        allow_redirects: bool = False,
        **kwargs: Any,
    ) -> ClientResponse:
        return await super()._request(
            *args,
            allow_redirects=allow_redirects,
            **kwargs,
        )

    @override
    def request(
        self,
        method: str,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().request(
            method,
            str_or_url,
            **kwargs,
        )

    @override
    def get(
        self,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().get(
            str_or_url,
            **kwargs,
        )

    @override
    def options(
        self,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().options(
            str_or_url,
            **kwargs,
        )

    @override
    def head(
        self,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().head(
            str_or_url,
            **kwargs,
        )

    @override
    def post(
        self,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().post(
            str_or_url,
            **kwargs,
        )

    @override
    def put(
        self,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().put(
            str_or_url,
            **kwargs,
        )

    @override
    def patch(
        self,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().patch(
            str_or_url,
            **kwargs,
        )

    @override
    def delete(
        self,
        str_or_url: StrOrURL,
        **kwargs: Unpack[_RequestOptions],
    ) -> _RequestContextManager:
        return super().delete(
            str_or_url,
            **kwargs,
        )


class RateLimitedClientSession(ClientSession):
    @override
    def __init__(
        self,
        rate_limiter: RateLimiter,
        **kwargs: Unpack[_ClientSessionOptions],
    ) -> None:
        super().__init__(**kwargs)

        self.__rate_limiter = rate_limiter

    @override
    async def _request(self, *args: Any, **kwargs: Any) -> ClientResponse:
        async with self.__rate_limiter:
            return await super()._request(*args, **kwargs)

    @override
    async def close(self) -> None:
        await super().close()

        self.__rate_limiter.close()


class _ExponentialRetryOptions(TypedDict, total=False):
    attempts: int
    start_timeout: float
    max_timeout: float
    factor: float
    statuses: set[int]
    exceptions: set[type[Exception]]
    methods: set[str]
    retry_all_server_errors: bool
    evaluate_response_callback: EvaluateResponseCallbackType


class RateLimitRetry(ExponentialRetry):
    @override
    def __init__(
        self,
        rate_limit_timeout: float,
        **kwargs: Unpack[_ExponentialRetryOptions],
    ) -> None:
        super().__init__(**kwargs)

        self.__rate_limit_timeout = rate_limit_timeout

    @override
    def get_timeout(
        self,
        attempt: int,
        response: ClientResponse | None = None,
    ) -> float:
        if response is not None and response.status in RATE_LIMIT_STATUS_CODES:
            self.attempts += 1
            return self.__rate_limit_timeout

        return super().get_timeout(attempt, response)


class RetryClientSession(RetryClient):
    def __init__(
        self,
        base_url: str | None = None,
        *,
        connection_limit: int | None = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> None:
        trace_configs = kwargs.pop("trace_configs", [trace_config])

        super().__init__(
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
        *args: Any,
        rate_limiter: RateLimiter,
        attempts: int = 3,
        connection_limit: int | None = None,
        start_timeout: float = 0.25,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            *args,
            **kwargs,
            client_session=RateLimitedClientSession(
                rate_limiter,
                **kwargs,
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
                rate_limit_timeout=rate_limiter.period,
                start_timeout=start_timeout,
            ),
        )

    async def response_callback(self, response: ClientResponse) -> bool:
        return response.status not in RATE_LIMIT_STATUS_CODES
