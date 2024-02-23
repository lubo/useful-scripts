import asyncio
from http import HTTPStatus
import itertools
import re
from urllib.parse import urlparse

from aiohttp import ClientError, ClientResponseError

from bookmarkmgr.aiohttp import RateLimitedRetryClientSession
from bookmarkmgr.logging import get_logger

from . import ClientSessionContextManagerMixin

logger = get_logger("bookmarkmgr/WM")

# Uncomment relevant errors when suppressing them is desired.
_IGNORED_ERRORS = {
    "error:no-access",
    # Some sites are permanently unavailable to WM
    # "error:service-unavailable",  # noqa: ERA001
    # 410 Gone is classified as unknown error
    # "error:unknown",  # noqa: ERA001
}


class WaybackMachineError(Exception):
    pass


class WaybackMachineClient(ClientSessionContextManagerMixin):
    def __init__(self) -> None:
        self._session = RateLimitedRetryClientSession(
            # $ for i in $(seq 25); do netcat web.archive.org 443 &; done; wait
            # ...
            # [26] 73113
            # [3]    exit 1     netcat web.archive.org 443
            # [6]    exit 1     netcat web.archive.org 443
            # [20]    exit 1     netcat web.archive.org 443
            # [12]    exit 1     netcat web.archive.org 443
            # [2]    exit 1     netcat web.archive.org 443
            connection_limit=20,
            # https://archive.org/details/toomanyrequests_20191110
            rate_limit=15,
            start_timeout=30,
        )

    async def _archive_page(  # noqa: C901
        self,
        url: str,
    ) -> tuple[str | None, str | None]:
        request_paramaters = {
            "url": url,
        }

        async with self._session.get(
            "https://archive.org/wayback/available",
            params=request_paramaters,
        ) as response:
            archived_snapshots = (await response.json())["archived_snapshots"]

        if (closest := archived_snapshots.get("closest")) is not None:
            logger.info("Archival entry found for %s", url)

            archival_url = closest["url"]

            if not archival_url.startswith("https://"):
                parsed_url = urlparse(archival_url)
                if parsed_url.scheme != "http":
                    logger.warning("Unexpected scheme: %s", url)
                archival_url = parsed_url._replace(scheme="https").geturl()

            return archival_url, None

        logger.debug("Requesting archival of %s", url)

        job_id_match = None
        request_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml",
        }

        try:
            # https://github.com/internetarchive/wayback-machine-webextension/blob/edebc9aa49c138fd784f94a1f70e47e0eb583dd9/webextension/scripts/background.js#L57
            async with self._session.post(
                "https://web.archive.org/save/",
                data=request_paramaters,
                headers=request_headers,
                params=request_paramaters,
            ) as response:
                # https://github.com/internetarchive/wayback-machine-webextension/blob/edebc9aa49c138fd784f94a1f70e47e0eb583dd9/webextension/scripts/background.js#L132
                job_id_match = re.search(
                    r"spn2-[a-z0-9-]*",
                    await response.text(),
                )
        except ClientResponseError as error:
            if error.status != HTTPStatus.NOT_FOUND.value:
                raise

        if job_id_match is None:
            async with self._session.get(
                f"https://web.archive.org/save/{url}",
                allow_redirects=False,
                headers=request_headers,
            ) as response:
                if response.status != HTTPStatus.FOUND.value:
                    message = (
                        f"Unexpected status code {response.status} for "
                        f"{response.url}"
                    )
                    raise WaybackMachineError(message) from None

                archival_url = response.headers["Location"]

                logger.info("Archived %s", url)

                return archival_url, None

        job_id = job_id_match.group()

        logger.debug("Archiving in progress for %s", url)

        delay_factor = 1

        # https://github.com/internetarchive/wayback-machine-webextension/blob/edebc9aa49c138fd784f94a1f70e47e0eb583dd9/webextension/scripts/background.js#L147
        for attempt in itertools.count(1):
            async with self._session.get(
                f"https://web.archive.org/save/status/{job_id}",
                headers=request_headers,
            ) as response:
                data = await response.json()

            match data.get("status"):
                case "pending":
                    delay = 6 * delay_factor

                    if delay_factor < 50:  # noqa: PLR2004
                        delay_factor = min(delay_factor * 2, 50)

                    logger.debug(
                        (
                            "Attempt %d: Rechecking archival status in %d "
                            "seconds for %s"
                        ),
                        attempt,
                        delay,
                        url,
                    )

                    await asyncio.sleep(delay)
                case "success":
                    break
                case _:
                    if data.get("status_ext") in _IGNORED_ERRORS:
                        return None, data["message"]

                    message = f"Unexpected status response for {url}: {data}"
                    raise WaybackMachineError(message)

        logger.info("Archived %s", url)

        return (
            f"https://web.archive.org/web/{data['timestamp']}/"
            f"{data['original_url']}"
        ), None

    async def archive_page(self, url: str) -> tuple[str | None, str | None]:
        try:
            return await self._archive_page(url)
        except ClientError as error:
            raise WaybackMachineError(error) from error
