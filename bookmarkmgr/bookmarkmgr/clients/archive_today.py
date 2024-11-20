import asyncio
from html.parser import HTMLParser
from http import HTTPStatus
import itertools

from bookmarkmgr.cronet import Error as CronetError
from bookmarkmgr.cronet import RateLimitedSession
from bookmarkmgr.logging import get_logger

from . import ClientSessionContextManagerMixin

logger = get_logger("bookmarkmgr/AT")


class ArchiveTodayError(Exception):
    pass


def _extract_text(html: str) -> str:
    text = ""

    def handle_data(data: str) -> None:
        nonlocal text

        text = " ".join(filter(len, [text, data.strip()]))

    html_parser = HTMLParser()
    html_parser.handle_data = handle_data  # type: ignore[method-assign]

    html_parser.feed(html)

    return text


class ArchiveTodayClient(ClientSessionContextManagerMixin):
    def __init__(self) -> None:
        self._session = RateLimitedSession(
            rate_limit=6,
        )

    async def _archive_page(self, url: str) -> tuple[str | None, str | None]:
        request_params = {
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
                            return None, _extract_text(response.text)

                        refresh_parts = refresh_header.split("=", 1)

                        if len(refresh_parts) > 1:
                            request_params = {
                                "url": refresh_parts[1],
                            }
                            submitted = True

                            logger.debug("Successfully submitted %s", url)
                case HTTPStatus.FOUND.value:
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

        logger.info("Archived %s", url)

        return (response.redirect_url, None)

    async def archive_page(self, url: str) -> tuple[str | None, str | None]:
        try:
            async with asyncio.timeout(1800):
                return await self._archive_page(url)
        except CronetError as error:
            raise ArchiveTodayError(error) from error
        except TimeoutError as error:
            message = "Operation timed out"
            raise ArchiveTodayError(message) from error
