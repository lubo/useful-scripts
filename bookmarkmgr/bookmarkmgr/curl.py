import asyncio

from curl_cffi.requests import AsyncSession, BrowserType, RequestsError

from .asyncio import RateLimiterMixin
from .logging import get_logger

logger = get_logger()


class InheritableCurlSession(AsyncSession):
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


class RetryCurlSession(InheritableCurlSession):
    def __init__(
        self,
        *args,
        rate_limit_timeout=60,
        impersonate=BrowserType.chrome110,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
            impersonate=impersonate,
        )

        self.rate_limit_timeout = rate_limit_timeout

    async def _request(self, *args, **kwargs):
        return await super().request(*args, **kwargs)

    async def request(self, method, url, *args, **kwargs):
        attempt = 1
        factor = 1
        max_attempts = 5
        rate_limit_status_codes = {408, 429}
        start_delay = 2.5

        while True:
            response = None
            error = None

            try:
                response = await self._request(method, url, *args, **kwargs)

                if not response.ok:
                    error = (
                        f"{response.status_code} {response.reason}: "
                        f"{method} {response.url}"
                    )

                if (
                    response.ok
                    or response.status_code not in rate_limit_status_codes
                    or attempt == max_attempts
                ):
                    if error is not None:
                        logger.error(error)

                    return response
            except RequestsError as err:
                error = f"{err}: {method} {url}"

                if attempt == max_attempts:
                    logger.error(error)

                    raise

            logger.debug(f"Attempt {attempt} failed: {error}")

            if response is None:
                delay = start_delay * factor
                factor *= 2
            elif response.status_code in rate_limit_status_codes:
                delay = self.rate_limit_timeout
                max_attempts += 1

            await asyncio.sleep(delay)

            attempt += 1


class RateLimitedRetryCurlSession(RateLimiterMixin, RetryCurlSession):
    def __init__(
        self,
        *args,
        rate_limit_period=60,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
            rate_limit_timeout=rate_limit_period,
        )

    async def _request(self, *args, **kwargs):
        async with self._rate_limiter:
            return await super()._request(*args, **kwargs)
