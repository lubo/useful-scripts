import asyncio
from http import HTTPStatus
from http.cookiejar import CookieJar
from itertools import chain
from typing import cast, override, Self, TYPE_CHECKING, TypedDict, Unpack

from yarl import URL

from bookmarkmgr.asyncio import RateLimiter, RateLimiterMixin

from ._cronet import lib
from .default_headers import DEFAULT_HEADERS
from .errors import (
    _raise_for_error_result,
    Error,
    NotContextManagerError,
    RequestError,
)
from .logging import logger
from .managers.executor import ExecutorManager
from .managers.request_callback import RequestCallbackManager
from .models import RequestParameters, Response
from .utils import adestroying, destroying

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping
    from http.client import HTTPResponse

    from .types import Engine, StrOrURL

INIT_MAX_RETRY_ATTEMPTS = 5

RATE_LIMIT_STATUS_CODES = {
    HTTPStatus.REQUEST_TIMEOUT.value,
    HTTPStatus.TOO_MANY_REQUESTS.value,
}

TRANSIENT_ERROR_STATUS_CODES = {
    HTTPStatus.FORBIDDEN.value,  # May be used for rate limiting.
    HTTPStatus.INTERNAL_SERVER_ERROR.value,
    HTTPStatus.BAD_GATEWAY.value,
    HTTPStatus.SERVICE_UNAVAILABLE.value,
    HTTPStatus.GATEWAY_TIMEOUT.value,
    # Cloudflare
    520,
    521,
    522,
    523,
    524,
    525,
    526,
    530,
}

RETRYABLE_STATUS_CODES = {
    *RATE_LIMIT_STATUS_CODES,
    *TRANSIENT_ERROR_STATUS_CODES,
}


class _SessionRequestOptions(TypedDict, total=False):
    params: Mapping[str, str]
    allow_redirects: bool


class Session:
    def __init__(self) -> None:
        self.cookie_jar = CookieJar()
        self._engine: Engine | None = None

    async def __aenter__(self) -> Self:
        self._open()

        return self

    async def __aexit__(
        self,
        *_: object,
    ) -> None:
        self.close()

    def _dispose_engine(self) -> None:
        if self._engine is None:
            return

        lib.Cronet_Engine_Destroy(self._engine)
        self._engine = None

    def _open(self) -> None:
        if self._engine is not None:
            return

        self._engine = lib.Cronet_Engine_Create()

        try:
            with destroying(
                lib.Cronet_EngineParams_Create(),
                lib.Cronet_EngineParams_Destroy,
            ) as params:
                lib.Cronet_EngineParams_enable_brotli_set(
                    params,
                    True,  # noqa: FBT003
                )
                lib.Cronet_EngineParams_enable_http2_set(
                    params,
                    True,  # noqa: FBT003
                )
                lib.Cronet_EngineParams_enable_quic_set(
                    params,
                    True,  # noqa: FBT003
                )

                _raise_for_error_result(
                    lib.Cronet_Engine_StartWithParams(self._engine, params),
                )
        except Error:
            self._dispose_engine()
            raise

    def close(self) -> None:
        if self._engine is None:
            return

        _raise_for_error_result(lib.Cronet_Engine_Shutdown(self._engine))
        self._dispose_engine()

    async def delete(
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        return await self.request("DELETE", url, **kwargs)

    async def get(
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        return await self.request("GET", url, **kwargs)

    async def head(
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        return await self.request("HEAD", url, **kwargs)

    async def options(
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        return await self.request("OPTIONS", url, **kwargs)

    async def patch(
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        return await self.request("PATCH", url, **kwargs)

    async def post(
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        return await self.request("POST", url, **kwargs)

    async def put(
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        return await self.request("PUT", url, **kwargs)

    async def request(
        self,
        method: str,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        if self._engine is None:
            raise NotContextManagerError

        allow_redirects = kwargs.get("allow_redirects", True)
        params = kwargs.get("params")

        # This option is preserved to preserve backwards compatibility.
        # However, support for redirects is currently not necessary and
        # removing it means that we don't have to deal with redirect
        # security issues like cookie handling and protocol downgrades.
        if allow_redirects:
            message = "Redirects are unsupported"
            raise ValueError(message)

        if isinstance(url, str):
            url = URL(url)

        if params is not None:
            url = url.update_query(params)

        url = str(url)

        request_params = RequestParameters(
            method=method,
            url=url,
        )
        self.cookie_jar.add_cookie_header(request_params)

        async with (
            adestroying(
                lib.Cronet_UrlRequestParams_Create(),
                lib.Cronet_UrlRequestParams_Destroy,
            ) as parameters,
            adestroying(
                lib.Cronet_UrlRequest_Create(),
                lib.Cronet_UrlRequest_Destroy,
            ) as request,
            RequestCallbackManager(
                request_params,
            ) as callback_manager,
            ExecutorManager() as executor_manager,
        ):
            lib.Cronet_UrlRequestParams_http_method_set(
                parameters,
                method.encode(),
            )
            for name, value in chain(
                DEFAULT_HEADERS,
                request_params.unredirected_hdrs.items(),
                request_params.headers.items(),
            ):
                with destroying(
                    lib.Cronet_HttpHeader_Create(),
                    lib.Cronet_HttpHeader_Destroy,
                ) as header:
                    lib.Cronet_HttpHeader_name_set(header, name.encode())
                    lib.Cronet_HttpHeader_value_set(header, value.encode())

                    lib.Cronet_UrlRequestParams_request_headers_add(
                        parameters,
                        header,
                    )

            _raise_for_error_result(
                lib.Cronet_UrlRequest_InitWithParams(
                    request,
                    self._engine,
                    url.encode(),
                    parameters,
                    callback_manager.callback,
                    executor_manager.executor,
                ),
            )

            _raise_for_error_result(lib.Cronet_UrlRequest_Start(request))

            try:
                response = await callback_manager.response()
            except:
                lib.Cronet_UrlRequest_Cancel(request)
                raise

        self.cookie_jar.extract_cookies(
            cast("HTTPResponse", response),
            request_params,
        )

        return response


class _RetrySessionOptions(TypedDict, total=False):
    rate_limit_timeout: float


type _RetryPredicate = Callable[[Response], Awaitable[bool]] | None


class _RetrySessionRequestOptions(_SessionRequestOptions, total=False):
    retry_predicate: _RetryPredicate


class RetrySession(Session):
    @override
    def __init__(
        self,
        **kwargs: Unpack[_RetrySessionOptions],
    ) -> None:
        super().__init__()

        self.__rate_limit_timeout = kwargs.get("rate_limit_timeout", 60)

    async def _request(
        self,
        method: str,
        url: StrOrURL,
        *,
        is_retry: bool,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        if is_retry:
            logger.debug("Retrying %s %s", method, url)

        return await super().request(method, url, **kwargs)

    if TYPE_CHECKING:

        @override
        async def delete(
            self,
            url: StrOrURL,
            **kwargs: Unpack[_RetrySessionRequestOptions],
        ) -> Response: ...

        @override
        async def get(
            self,
            url: StrOrURL,
            **kwargs: Unpack[_RetrySessionRequestOptions],
        ) -> Response: ...

        @override
        async def head(
            self,
            url: StrOrURL,
            **kwargs: Unpack[_RetrySessionRequestOptions],
        ) -> Response: ...

        @override
        async def options(
            self,
            url: StrOrURL,
            **kwargs: Unpack[_RetrySessionRequestOptions],
        ) -> Response: ...

        @override
        async def patch(
            self,
            url: StrOrURL,
            **kwargs: Unpack[_RetrySessionRequestOptions],
        ) -> Response: ...

        @override
        async def post(
            self,
            url: StrOrURL,
            **kwargs: Unpack[_RetrySessionRequestOptions],
        ) -> Response: ...

        @override
        async def put(
            self,
            url: StrOrURL,
            **kwargs: Unpack[_RetrySessionRequestOptions],
        ) -> Response: ...

    @override
    async def request(  # noqa: C901
        self,
        method: str,
        url: StrOrURL,
        *,
        retry_predicate: _RetryPredicate = None,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        attempt = 1
        factor = 1
        is_retry = False
        max_attempts = INIT_MAX_RETRY_ATTEMPTS
        start_delay = 2.5

        while True:
            response = None
            retry = False
            error = None

            try:
                response = await self._request(
                    method,
                    url,
                    is_retry=is_retry,
                    **kwargs,
                )

                if retry_predicate is not None:
                    retry = await retry_predicate(response)

                if retry:
                    error = (
                        f"Retry triggered by caller: {method} {response.url}"
                    )
                elif not response.ok:
                    error = (
                        f"{response.status_code} {response.reason}: "
                        f"{method} {response.url}"
                    )

                if not retry and (
                    response.status_code not in RETRYABLE_STATUS_CODES
                    or attempt == max_attempts
                ):
                    if error is not None:
                        logger.debug(error)

                    return response
            except RequestError as err:
                error = str(err)

                if attempt == max_attempts:
                    logger.debug(error)

                    raise

            is_retry = True

            if error is not None:
                log_function = (
                    logger.warning
                    if attempt % (INIT_MAX_RETRY_ATTEMPTS * 2) == 0
                    else logger.debug
                )
                log_function(
                    "Attempt %d/%d failed: %s",
                    attempt,
                    max_attempts,
                    error,
                )

            if not retry and (
                response is None
                or response.status_code not in RATE_LIMIT_STATUS_CODES
            ):
                delay = start_delay * factor
                factor *= 2
            else:
                delay = self.__rate_limit_timeout
                max_attempts += 1

            await asyncio.sleep(delay)

            attempt += 1


class RateLimitedSession(RateLimiterMixin, RetrySession):
    @override
    def __init__(
        self,
        *,
        rate_limit: int,
        rate_limit_period: float = 60,
    ) -> None:
        super().__init__(
            rate_limit=rate_limit,
            rate_limit_period=rate_limit_period,
            rate_limit_timeout=rate_limit_period,
        )

    @override
    async def _request(
        self,
        method: str,
        url: StrOrURL,
        *,
        is_retry: bool,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        async with self._RateLimiterMixin_rate_limiter:
            return await super()._request(
                method,
                url,
                is_retry=is_retry,
                **kwargs,
            )

    @override
    def close(self) -> None:
        super().close()

        self._RateLimiterMixin_rate_limiter.close()


class PerHostnameRateLimitedSession(RetrySession):
    @override
    def __init__(
        self,
        *,
        host_rate_limits: Iterable[tuple[str, int, float, float]],
        **kwargs: Unpack[_RetrySessionOptions],
    ) -> None:
        super().__init__(**kwargs)

        self.__rate_limiters = {
            hostname.lower(): RateLimiter(limit, period, jitter)
            for hostname, limit, period, jitter in host_rate_limits
        }

    @override
    async def _request(
        self,
        method: str,
        url: StrOrURL,
        *,
        is_retry: bool,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        if isinstance(url, str):
            url = URL(url)

        if url.host is None:
            message = "Missing hostname in the URL"
            raise ValueError(message)

        hostname = url.host.lower()

        if hostname not in self.__rate_limiters:
            self.__rate_limiters[hostname] = RateLimiter(1, 1)

        async with self.__rate_limiters[hostname]:
            return await super()._request(
                method,
                url,
                is_retry=is_retry,
                **kwargs,
            )

    @override
    def close(self) -> None:
        super().close()

        for rate_limiter in self.__rate_limiters.values():
            rate_limiter.close()
