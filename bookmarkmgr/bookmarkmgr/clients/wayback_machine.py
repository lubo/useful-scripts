import asyncio
import re
import sys
from urllib.parse import urlparse

from aiohttp import ClientResponseError

from . import ClientSessionContextManagerMixin
from ..aiohttp import RateLimitedRetryClientSession
from ..logging import get_logger

logger = get_logger("bookmarkmgr:WM")


class WaybackMachineClient(ClientSessionContextManagerMixin):
    def __init__(self):
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

    async def archive_page(self, url):
        request_paramaters = {
            "url": url,
        }

        async with self._session.get(
            "https://archive.org/wayback/available",
            params=request_paramaters,
        ) as response:
            archived_snapshots = (await response.json())["archived_snapshots"]

        if (closest := archived_snapshots.get("closest")) is not None:
            logger.info(f"Archival entry found for {url}")

            archival_url = closest["url"]

            if not archival_url.startswith("https://"):
                parsed_url = urlparse(archival_url)
                if parsed_url.scheme != "http":
                    logger.warn(f"Unexpected scheme: {url}")
                archival_url = parsed_url._replace(scheme="https").geturl()

            return archival_url, None

        logger.debug(f"Requesting archival of {url}")

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
                job_id = re.search(r"spn2-[a-z0-9-]*", await response.text())

                if job_id is None:
                    return None, "Job ID not found"
        except ClientResponseError as error:
            if error.status != 404:
                raise

            async with self._session.get(
                f"https://web.archive.org/save/{url}",
                allow_redirects=False,
                headers=request_headers,
            ) as response:
                assert response.status == 302

                archival_url = response.headers["Location"]

                logger.info(f"Archived {archival_url}")

                return archival_url, None

        job_id = job_id.group()

        logger.debug(f"Archiving in progress for {url}")

        delay_factor = 1

        # https://github.com/internetarchive/wayback-machine-webextension/blob/edebc9aa49c138fd784f94a1f70e47e0eb583dd9/webextension/scripts/background.js#L147
        for attempt in range(1, sys.maxsize):
            async with self._session.get(
                f"https://web.archive.org/save/status/{job_id}",
                headers=request_headers,
            ) as response:
                data = await response.json()

            match data.get("status"):
                case "pending":
                    delay = 6 * delay_factor

                    if delay_factor < 50:
                        delay_factor = min(delay_factor * 2, 50)

                    logger.debug(
                        (
                            f"Attempt {attempt}: Rechecking archival status "
                            f"in {delay} seconds for {url}"
                        ),
                    )

                    await asyncio.sleep(delay)

                    continue
                case "success":
                    logger.info(f"Archived {url}")

                    return (
                        f"https://web.archive.org/web/{data['timestamp']}/"
                        f"{data['original_url']}"
                    ), None
                case _:
                    return None, data["message"]