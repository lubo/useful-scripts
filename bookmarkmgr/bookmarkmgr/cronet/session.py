import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import nullcontext
from http import HTTPStatus
from typing import Any, Self, TYPE_CHECKING
from urllib.parse import urlparse

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
    from .types import Engine

INIT_MAX_RETRY_ATTEMPTS = 5

RATE_LIMIT_STATUS_CODES = {
    HTTPStatus.REQUEST_TIMEOUT.value,
    HTTPStatus.TOO_MANY_REQUESTS.value,
}

TRANSIENT_ERROR_STATUS_CODES = {
    HTTPStatus.INTERNAL_SERVER_ERROR.value,
    HTTPStatus.BAD_GATEWAY.value,
    HTTPStatus.SERVICE_UNAVAILABLE.value,
    HTTPStatus.GATEWAY_TIMEOUT.value,
}

RETRYABLE_STATUS_CODES = {
    *RATE_LIMIT_STATUS_CODES,
    *TRANSIENT_ERROR_STATUS_CODES,
}


class Session:
    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._executor_manager = ExecutorManager()

    async def __aenter__(self) -> Self:
        await self._executor_manager.__aenter__()
        self._open()

        return self

    async def __aexit__(
        self,
        *args: Any,  # noqa: PYI036
        **kwargs: Any,
    ) -> None:
        try:
            await self._executor_manager.__aexit__(*args, **kwargs)
        finally:
            self._close()

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
                _raise_for_error_result(
                    lib.Cronet_Engine_StartWithParams(self._engine, params),
                )
        except Error:
            self._dispose_engine()
            raise

    def _close(self) -> None:
        if self._engine is None:
            return

        _raise_for_error_result(lib.Cronet_Engine_Shutdown(self._engine))
        self._dispose_engine()

    async def delete(self, *args: Any, **kwargs: Any) -> Response:
        return await self.request("DELETE", *args, **kwargs)

    async def get(self, *args: Any, **kwargs: Any) -> Response:
        return await self.request("GET", *args, **kwargs)

    async def head(self, *args: Any, **kwargs: Any) -> Response:
        return await self.request("HEAD", *args, **kwargs)

    async def options(self, *args: Any, **kwargs: Any) -> Response:
        return await self.request("OPTIONS", *args, **kwargs)

    async def patch(self, *args: Any, **kwargs: Any) -> Response:
        return await self.request("PATCH", *args, **kwargs)

    async def post(self, *args: Any, **kwargs: Any) -> Response:
        return await self.request("POST", *args, **kwargs)

    async def put(self, *args: Any, **kwargs: Any) -> Response:
        return await self.request("PUT", *args, **kwargs)

    async def request(
        self,
        method: str,
        url: str,
        params: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> Response:
        if self._engine is None:
            raise NotContextManagerError

        if params is not None:
            url = str(URL(url).update_query(params))

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
                RequestParameters(
                    method=method,
                    url=url,
                    **kwargs,
                ),
            ) as callback_manager,
        ):
            lib.Cronet_UrlRequestParams_http_method_set(
                parameters,
                method.encode(),
            )
            for name, value in DEFAULT_HEADERS:
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
                    self._executor_manager.executor,
                ),
            )

            _raise_for_error_result(lib.Cronet_UrlRequest_Start(request))

            try:
                return await callback_manager.response()
            except:
                lib.Cronet_UrlRequest_Cancel(request)
                raise


class RetrySession(Session):
    def __init__(
        self,
        *args: Any,
        rate_limit_timeout: float = 60,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            *args,
            **kwargs,
        )

        self.__rate_limit_timeout = rate_limit_timeout

    async def _request(
        self,
        method: str,
        url: str,
        *args: Any,
        is_retry: bool,
        **kwargs: Any,
    ) -> Response:
        if is_retry:
            logger.debug("Retrying %s %s", method, url)

        return await super().request(method, url, *args, **kwargs)

    async def request(  # noqa: C901
        self,
        method: str,
        url: str,
        *args: Any,
        retry_predicate: Callable[[Response], Awaitable[bool]] | None = None,
        **kwargs: Any,
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
                    *args,
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
                    logger.warn
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
    def __init__(
        self,
        *args: Any,
        rate_limit: int,
        rate_limit_period: float = 60,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            *args,
            **kwargs,
            rate_limit=rate_limit,
            rate_limit_period=rate_limit_period,
            rate_limit_timeout=rate_limit_period,
        )

    async def _request(self, *args: Any, **kwargs: Any) -> Response:
        async with self._RateLimiterMixin_rate_limiter:
            return await super()._request(*args, **kwargs)

    def _close(self) -> None:
        super()._close()

        self._RateLimiterMixin_rate_limiter.close()


class PerHostnameRateLimitedSession(RetrySession):
    def __init__(
        self,
        *args: Any,
        host_rate_limits: Iterable[tuple[str, int, float, float]],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.__null_context = nullcontext()
        self.__rate_limiters = {
            hostname.lower(): RateLimiter(limit, period, jitter)
            for hostname, limit, period, jitter in host_rate_limits
        }

    async def _request(
        self,
        method: str,
        url: str,
        *args: Any,
        **kwargs: Any,
    ) -> Response:
        parsed_url = urlparse(url)

        if parsed_url.hostname is None:
            message = "Missing hostname in the URL"
            raise ValueError(message)

        async with self.__rate_limiters.get(
            parsed_url.hostname.lower(),
            self.__null_context,
        ):
            return await super()._request(method, url, *args, **kwargs)

    def _close(self) -> None:
        super()._close()

        for rate_limiter in self.__rate_limiters.values():
            rate_limiter.close()
