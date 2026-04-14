import asyncio
import contextlib
from http import HTTPStatus
import re
from typing import override, Self, TYPE_CHECKING, TypedDict, Unpack

from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    # Playwright crashes without Route type present with:
    #
    #   NameError: Page.__aexit__: name 'Route' is not defined
    Route,
)
from yarl import URL

from bookmarkmgr.asyncio import RateLimiter

from .errors import (
    NotContextManagerError,
    RequestError,
)
from .logging import logger
from .models import Response

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping

    from playwright.async_api import (
        Browser,
        BrowserContext,
    )

    from .types import StrOrURL

_PAGE_GOTO_ERROR_PATTERN = re.compile(
    r"^Page.goto: (\S+) at (\S+)$",
    re.MULTILINE,
)

_USER_AGENT_PATTERN = re.compile(
    (
        r"^(Mozilla/5\.0 \(.+\) AppleWebKit/537\.36 \(KHTML, like Gecko\) )"
        r"(Headless)?(Chrome/\d+\.0\.0\.0 Safari/537\.36)$"
    ),
)

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

# 2x Chrome maximum, lets the browser time out first.
TIMEOUT = 10 * 60 * 1000


class _SessionRequestOptions(TypedDict, total=False):
    params: Mapping[str, str]
    allow_redirects: bool


class Session:
    def __init__(self, browser: Browser) -> None:
        self._browser = browser
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Self:
        await self._open()

        return self

    async def __aexit__(
        self,
        *_: object,
    ) -> None:
        await self.close()

    async def _open(self) -> None:
        if self._context is not None:
            return

        async with await self._browser.new_page() as page:
            user_agent = await page.evaluate("navigator.userAgent")
        if (user_agent_match := _USER_AGENT_PATTERN.match(user_agent)) is None:
            msg = f"Unexpected user agent: {user_agent}"
            raise ValueError(msg)
        user_agent = user_agent_match.group(1) + user_agent_match.group(3)

        self._context = await self._browser.new_context(
            accept_downloads=False,
            java_script_enabled=False,
            service_workers="block",
            user_agent=user_agent,
        )

    async def close(self) -> None:
        if self._context is None:
            return

        await self._context.close()
        self._context = None

    async def get(  # noqa: C901
        self,
        url: StrOrURL,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        if self._context is None:
            raise NotContextManagerError

        allow_redirects = kwargs.get("allow_redirects", True)
        params = kwargs.get("params")

        if isinstance(url, str):
            url = URL(url)

        if params is not None:
            url = url.update_query(params)

        async def handle_route(route: Route) -> None:
            # Browser may rewrite URLs like https://example.com to
            # https://example.com/, etc.
            if URL(route.request.url) == url:
                await route.continue_()
                return

            await route.abort(error_code="aborted")

        async with await self._context.new_page() as page:
            await page.route("**/*", handle_route)

            # Using BrowserContext.request.fetch()/Page.request.fetch()/
            # Route.fetch() would be more efficient, but it doesn't behave the
            # same way as normal navigation requests do and it cannot be forced
            # to behave like that. Names, order and values of headers cannot be
            # entirely controlled, HTTP/2/3 doesn't work, etc.
            try:
                internal_response = await page.goto(
                    str(url),
                    timeout=TIMEOUT,
                    wait_until="commit",
                )
            except PlaywrightError as error:
                if (
                    match := _PAGE_GOTO_ERROR_PATTERN.match(error.message)
                ) is None:
                    message = (
                        "Unrecognized error message: "
                        + error.message.splitlines()[0]
                    )
                    raise ValueError(message) from None
                error_code, error_url = match.group(1, 2)
                message = f"{error_code}: GET {error_url}"
                raise RequestError(message) from error

            if internal_response is None:
                msg = f"Invalid URL: {url}"
                raise ValueError(msg)

            redirect_url = None

            if not allow_redirects:
                # There doesn't seem to be a way to actually prevent the
                # browser from following redirects. Route handler is only fired
                # for the initial URL.
                while (
                    (
                        redirected_from
                        := internal_response.request.redirected_from
                    )
                    is not None
                    and (redirect_response := await redirected_from.response())
                    is not None
                    # Redirect is not internal.
                    and await redirect_response.server_addr()
                ):
                    if redirect_url is None:
                        redirect_url = internal_response.url

                    internal_response = redirect_response

            if not (status_text := internal_response.status_text):
                with contextlib.suppress(ValueError):
                    status_text = HTTPStatus(internal_response.status).phrase

            response = Response(
                internal_response.status,
                internal_response.url,
                status_text,
                redirect_url=redirect_url,
            )
            if redirect_url is None:
                response.content = await internal_response.body()
            for header in await internal_response.headers_array():
                response.headers[header["name"]] = header["value"]

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
        browser: Browser,
        **kwargs: Unpack[_RetrySessionOptions],
    ) -> None:
        super().__init__(browser)

        self.__rate_limit_timeout = kwargs.get("rate_limit_timeout", 60)

    async def _get(
        self,
        url: StrOrURL,
        *,
        is_retry: bool,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        if is_retry:
            logger.debug("Retrying GET %s", url)

        return await super().get(url, **kwargs)

    @override
    async def get(  # noqa: C901
        self,
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
                response = await self._get(
                    url,
                    is_retry=is_retry,
                    **kwargs,
                )

                if retry_predicate is not None:
                    retry = await retry_predicate(response)

                if retry:
                    error = f"Retry triggered by caller: GET {response.url}"
                elif not response.ok:
                    error = (
                        f"{response.status_code} {response.reason}: "
                        f"GET {response.url}"
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


class RateLimitedSession(RetrySession):
    def __init__(
        self,
        browser: Browser,
        rate_limiter: RateLimiter,
    ) -> None:
        super().__init__(
            browser,
            rate_limit_timeout=rate_limiter.period,
        )

        self._rate_limiter = rate_limiter

    @override
    async def _get(
        self,
        url: StrOrURL,
        *,
        is_retry: bool,
        **kwargs: Unpack[_SessionRequestOptions],
    ) -> Response:
        async with self._rate_limiter:
            return await super()._get(
                url,
                is_retry=is_retry,
                **kwargs,
            )

    @override
    async def close(self) -> None:
        await super().close()

        self._rate_limiter.close()


class PerHostnameRateLimitedSession(RetrySession):
    def __init__(
        self,
        browser: Browser,
        *,
        host_rate_limits: Iterable[tuple[str, int, float, float]],
        **kwargs: Unpack[_RetrySessionOptions],
    ) -> None:
        super().__init__(browser, **kwargs)

        self.__rate_limiters = {
            hostname.lower(): RateLimiter(limit, period, jitter)
            for hostname, limit, period, jitter in host_rate_limits
        }

    @override
    async def _get(
        self,
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
            return await super()._get(
                url,
                is_retry=is_retry,
                **kwargs,
            )

    @override
    async def close(self) -> None:
        await super().close()

        for rate_limiter in self.__rate_limiters.values():
            rate_limiter.close()
