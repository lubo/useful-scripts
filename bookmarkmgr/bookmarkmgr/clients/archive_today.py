import asyncio
from html.parser import HTMLParser
from http import HTTPStatus
import itertools
from typing import NotRequired, override, TYPE_CHECKING, TypedDict

from bookmarkmgr.asyncio import RateLimiter
from bookmarkmgr.cronet import Error as CronetError
from bookmarkmgr.cronet import RateLimitedSession
from bookmarkmgr.logging import get_logger
from bookmarkmgr.types import Failure, Result, Success

from . import ClientSessionContextManagerMixin

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger("bookmarkmgr/AT")


class ArchiveTodayError(Exception):
    pass


class _HtmlParser(HTMLParser):
    __text: str

    @override
    def handle_data(self, data: str) -> None:
        # type: ignore[no-matching-overload]
        self.__text = " ".join(filter(len, [self.__text, data.strip()]))

    @override
    def reset(self) -> None:
        super().reset()

        self.__text = ""

    @property
    def text(self) -> str:
        return self.__text


def _extract_text(html: str) -> str:
    html_parser = _HtmlParser()
    html_parser.feed(html)

    return html_parser.text


class _RequestParams(TypedDict):
    url: str
    params: NotRequired[Mapping[str, str]]


class ArchiveTodayClient(ClientSessionContextManagerMixin[RateLimitedSession]):
    def __init__(self) -> None:
        self._session = RateLimitedSession(
            rate_limiter=RateLimiter(
                limit=6,
            ),
        )

    async def _archive_page(self, url: str) -> Result[str, str]:
        archival_url = None
        request_params: _RequestParams = {
            "url": "https://archive.ph/submit/",
            "params": {
                "url": url,
            },
        }
        submitted = False

        start_delay = 5
        delay_factor = 0

        for attempt in itertools.count():
            response = await self._session.get(
                **request_params,
                allow_redirects=False,
            )

            match response.status_code:
                case HTTPStatus.OK.value:
                    if not submitted:
                        refresh_header = response.headers.get("Refresh")

                        if refresh_header is None:
                            return Failure(_extract_text(response.text))

                        refresh_parts = refresh_header.split("=", 1)

                        if len(refresh_parts) > 1:
                            request_params = {
                                "url": refresh_parts[1],
                            }
                            submitted = True

                            logger.debug("Successfully submitted %s", url)
                case HTTPStatus.FOUND.value:
                    archival_url = response.redirect_url
                    break
                case _:
                    message = (
                        f"Unexpected status code {response.status_code} "
                        f"for {response.url}"
                    )
                    raise ArchiveTodayError(message)

            if (delay := start_delay * delay_factor) > 0:
                logger.debug(
                    (
                        "Attempt %d: Rechecking archival status in %d seconds "
                        "for %s"
                    ),
                    attempt,
                    delay,
                    url,
                )

                if delay_factor < 60:  # noqa: PLR2004
                    delay_factor = min(delay_factor * 2, 60)

                await asyncio.sleep(delay)
            else:
                delay_factor = 1

        if archival_url is None:
            message = f"Failed to obtain archival URL for {url}"
            raise ArchiveTodayError(message)

        logger.info("Archived %s", url)

        return Success(archival_url)

    async def archive_page(self, url: str) -> Result[str, str]:
        try:
            async with asyncio.timeout(3600):
                return await self._archive_page(url)
        except CronetError as error:
            raise ArchiveTodayError(error) from error
        except TimeoutError as error:
            message = "Operation timed out"
            raise ArchiveTodayError(message) from error
