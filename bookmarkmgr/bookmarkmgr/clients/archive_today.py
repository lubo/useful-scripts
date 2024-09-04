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
        response = await self._session.get(
            "https://archive.ph/submit/",
            allow_redirects=False,
            params={
                "url": url,
            },
        )

        if response.status_code == HTTPStatus.OK.value:
            if "Refresh" not in response.headers:
                return None, _extract_text(response.text)

            refresh_header = response.headers["Refresh"]
            try:
                _, wip_url = refresh_header.split("=", 1)
            except ValueError as error:
                message = (
                    f"Malformed Refresh header: '{refresh_header}': "
                    f"{response.url}"
                )
                raise ArchiveTodayError(message) from error

            logger.debug("Successfully submitted %s", url)

        start_delay = 5
        delay_factor = 0

        for attempt in itertools.count():
            match response.status_code:
                case HTTPStatus.OK.value:
                    pass
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

            response = await self._session.get(
                wip_url,
                allow_redirects=False,
            )

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
