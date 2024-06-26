from collections.abc import Awaitable
from enum import IntEnum, unique
from http import HTTPStatus
import re
from urllib.parse import ParseResult, quote, urlparse

from bookmarkmgr.cronet import RequestError, Response
from bookmarkmgr.scraper import Page

REDIRECT_STATUS_CODES = {
    HTTPStatus.MOVED_PERMANENTLY.value,
    HTTPStatus.FOUND.value,
    HTTPStatus.TEMPORARY_REDIRECT.value,
    HTTPStatus.PERMANENT_REDIRECT.value,
}

NOT_FOUND_STATUS_CODES = {
    HTTPStatus.NOT_FOUND.value,
    HTTPStatus.GONE.value,
}


@unique
class LinkStatus(IntEnum):
    OK = 1
    BROKEN = 2
    POSSIBLY_BROKEN = 3
    BLOCKED = 4


async def check_link_status(
    get_page_awaitable: Awaitable[tuple[Page | None, Response]],
) -> tuple[LinkStatus, None | str]:
    link_status = LinkStatus.OK
    error = None

    try:
        page, response = await get_page_awaitable
    except RequestError as e:
        link_status = LinkStatus.POSSIBLY_BROKEN
        error = str(e)

        return link_status, error

    match response.status_code:
        case HTTPStatus.OK.value:
            if page is None:
                message = "Page is None"
                raise ValueError(message)

            if page.title == "Video deleted":
                link_status = LinkStatus.BROKEN
                error = page.title
            elif (
                match := re.fullmatch(
                    r"(Post Not Found) \[[0-9a-f]+\] - [a-zA-Z]{8}",
                    page.title,
                )
            ) is not None:
                link_status = LinkStatus.BROKEN
                error = match.group(1)
        case HTTPStatus.UNAUTHORIZED.value | HTTPStatus.FORBIDDEN.value:
            link_status = LinkStatus.BLOCKED

    if error is not None or (
        response.ok and response.status_code not in REDIRECT_STATUS_CODES
    ):
        return link_status, error

    error = f"{response.status_code} {response.reason}"

    if link_status != LinkStatus.OK:
        return link_status, error

    link_status = LinkStatus.POSSIBLY_BROKEN

    return link_status, error


def _get_quoted_url_matching_redirect(
    response: Response,
    parsed_url: ParseResult,
) -> str | None:
    if response.status_code not in REDIRECT_STATUS_CODES:
        return None

    quoted_url = parsed_url._replace(path=quote(parsed_url.path)).geturl()

    if response.redirect_url == quoted_url:
        return quoted_url

    return None


def get_fixed_url(response: Response, url: str) -> None | str:
    if (
        response.status_code
        not in REDIRECT_STATUS_CODES | NOT_FOUND_STATUS_CODES
    ):
        return None

    parsed_url = urlparse(url)

    if (
        quoted_url := _get_quoted_url_matching_redirect(response, parsed_url)
    ) is not None:
        return quoted_url

    # Raindrop breaks some links during import by removing trailing slash and
    # by adding trailing slash during new link addition.
    potentially_fixed_parsed_url = parsed_url._replace(
        path=(
            parsed_url.path.rstrip("/")
            if parsed_url.path.endswith("/")
            else f"{parsed_url.path}/"
        ),
    )

    if (
        quoted_url := _get_quoted_url_matching_redirect(
            response,
            potentially_fixed_parsed_url,
        )
    ) is not None:
        return quoted_url

    potentially_fixed_url = potentially_fixed_parsed_url.geturl()

    if (
        response.status_code in REDIRECT_STATUS_CODES
        and response.redirect_url != potentially_fixed_url
    ):
        return None

    return potentially_fixed_url
