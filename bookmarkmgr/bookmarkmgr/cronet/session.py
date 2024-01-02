import asyncio
from contextlib import nullcontext
from http import HTTPStatus
from urllib.parse import urlparse

from yarl import URL

from bookmarkmgr.asyncio import RateLimiter, RateLimiterMixin
from bookmarkmgr.logging import get_logger

from ._cronet import lib
from .default_headers import DEFAULT_HEADERS
from .errors import _raise_for_error_result, Error, RequestError
from .managers.executor import ExecutorManager
from .managers.request_callback import RequestCallbackManager
from .models import RequestParameters
from .utils import adestroying, destroying

logger = get_logger()

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
    def __init__(self):
        self._engine = None

        self._open()

    async def __aenter__(self):
        self._open()

        return self

    async def __aexit__(self, *args, **kwargs):
        self.close()

    def _dispose_engine(self):
        lib.Cronet_Engine_Destroy(self._engine)
        self._engine = None

    def _open(self):
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

    def close(self):
        _raise_for_error_result(lib.Cronet_Engine_Shutdown(self._engine))
        self._dispose_engine()

    async def delete(self, *args, **kwargs):
        return await self.request("DELETE", *args, **kwargs)

    async def get(self, *args, **kwargs):
        return await self.request("GET", *args, **kwargs)

    async def head(self, *args, **kwargs):
        return await self.request("HEAD", *args, **kwargs)

    async def options(self, *args, **kwargs):
        return await self.request("OPTIONS", *args, **kwargs)

    async def patch(self, *args, **kwargs):
        return await self.request("PATCH", *args, **kwargs)

    async def post(self, *args, **kwargs):
        return await self.request("POST", *args, **kwargs)

    async def put(self, *args, **kwargs):
        return await self.request("PUT", *args, **kwargs)

    async def request(self, method, url, params=None, **kwargs):
        if params is not None:
            url = str(URL(url).update_query(params))

        executor_manager = ExecutorManager()

        def on_request_finished():
            executor_manager.shutdown()

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
                on_request_finished,
            ) as callback_manager,
            executor_manager,
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
                    executor_manager.executor,
                ),
            )

            _raise_for_error_result(lib.Cronet_UrlRequest_Start(request))

        if callback_manager.result_error is not None:
            raise callback_manager.result_error

        if callback_manager.request_error is not None:
            raise callback_manager.request_error

        return callback_manager.response


class RetrySession(Session):
    def __init__(
        self,
        *args,
        rate_limit_timeout=60,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.rate_limit_timeout = rate_limit_timeout

    async def _request(self, method, url, *args, is_retry, **kwargs):
        if is_retry:
            logger.debug("Retrying %s %s", method, url)

        return await super().request(method, url, *args, **kwargs)

    async def request(  # noqa: C901
        self,
        method,
        url,
        *args,
        retry_predicate=None,
        **kwargs,
    ):
        attempt = 1
        factor = 1
        is_retry = False
        max_attempts = 5
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
                error = f"{err}: {method} {url}"

                if attempt == max_attempts:
                    logger.debug(error)

                    raise

            is_retry = True

            if error is not None:
                logger.debug(
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
                delay = self.rate_limit_timeout
                max_attempts += 1

            await asyncio.sleep(delay)

            attempt += 1


class RateLimitedSession(RateLimiterMixin, RetrySession):
    def __init__(
        self,
        *args,
        rate_limit,
        rate_limit_period=60,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
            rate_limit=rate_limit,
            rate_limit_period=rate_limit_period,
            rate_limit_timeout=rate_limit_period,
        )

    async def _request(self, *args, **kwargs):
        async with self._rate_limiter:
            return await super()._request(*args, **kwargs)


class PerHostnameRateLimitedSession(RetrySession):
    def __init__(
        self,
        *args,
        host_rate_limits,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self._rate_limiters = {
            hostname.lower(): RateLimiter(limit, period)
            for hostname, limit, period in host_rate_limits
        }
        self._rate_limiters[None] = nullcontext()

    async def _request(self, method, url, *args, **kwargs):
        async with self._rate_limiters.get(
            urlparse(url).hostname.lower(),
            self._rate_limiters[None],
        ):
            return await super()._request(method, url, *args, **kwargs)
