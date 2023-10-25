import asyncio
from html.parser import HTMLParser
import sys

from . import ClientSessionContextManagerMixin
from ..cronet import RateLimitedSession
from ..logging import get_logger

logger = get_logger("bookmarkmgr:AT")


class TextExtractionHTMLParser(HTMLParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.text_parts = []

    def get_text(self):
        return " ".join(self.text_parts)

    def handle_data(self, data):
        self.text_parts.append(data.strip())


class ArchiveTodayClient(ClientSessionContextManagerMixin):
    def __init__(self):
        self._session = RateLimitedSession(
            rate_limit=6,
        )

    async def archive_page(self, url):
        response = await self._session.get(
            "https://archive.ph/submit/",
            allow_redirects=False,
            params={
                "url": url,
            },
        )

        if response.status_code == 200:
            if "Refresh" not in response.headers:
                html_parser = TextExtractionHTMLParser()
                html_parser.feed(response.text)

                return None, html_parser.get_text()

            refresh_header = response.headers["Refresh"]
            try:
                _, wip_url = refresh_header.split("=", 1)
            except ValueError as error:
                raise Exception(
                    (
                        f"Malformed Refresh header: '{refresh_header}': "
                        f"{response.url}"
                    ),
                ) from error

            logger.debug(f"Successfully submitted {url}")

        start_delay = 5
        delay_factor = 0

        for attempt in range(sys.maxsize):
            match response.status_code:
                case 200:
                    pass
                case 302:
                    logger.info(f"Archived {url}")

                    return (response.redirect_url, None)
                case _:
                    raise Exception(
                        (
                            f"Unexpected status code {response.status_code} "
                            f"for {response.url}"
                        ),
                    )

            if (delay := start_delay * delay_factor) > 0:
                logger.debug(
                    (
                        f"Attempt {attempt}: Rechecking archival status in "
                        f"{delay} seconds for {url}"
                    ),
                )

                if delay_factor < 60:
                    delay_factor = min(delay_factor * 2, 60)

                await asyncio.sleep(delay)
            else:
                delay_factor = 1

            response = await self._session.get(
                wip_url,
                allow_redirects=False,
            )
