from enum import IntEnum, unique
from http import HTTPStatus
import itertools
import re
from typing import Any, cast, Protocol, TYPE_CHECKING
from urllib.parse import ParseResult, quote, urlparse

import tld
from tld import get_tld

from bookmarkmgr.cronet import RequestError

if TYPE_CHECKING:
    from bookmarkmgr import scraper

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


def _get_tld_result_lax(url: str) -> tld.Result | None:
    return cast(
        "tld.Result | None",
        get_tld(
            url,
            as_object=True,
            fail_silently=True,
            fix_protocol=True,
        ),
    )


def _remove_www_subdomain(domain: str) -> str:
    return re.sub(
        r"^www\d*(\.|$)",
        "",
        domain,
        flags=re.IGNORECASE,
    )


@unique
class LinkStatus(IntEnum):
    OK = 1
    BROKEN = 2
    POSSIBLY_BROKEN = 3
    BLOCKED = 4


def check_link_status(
    scraper_result: scraper.Result,
) -> tuple[LinkStatus, None | str]:
    link_status = LinkStatus.OK
    error = None

    if isinstance(scraper_result, RequestError):
        link_status = LinkStatus.POSSIBLY_BROKEN
        error = str(scraper_result)

        return link_status, error

    match scraper_result.response.status_code:
        case HTTPStatus.OK.value:
            if scraper_result.page is None:
                message = "Page is None"
                raise ValueError(message)

            if scraper_result.page.title == "Video deleted":
                link_status = LinkStatus.BROKEN
                error = scraper_result.page.title
            elif (
                match := re.fullmatch(
                    r"(Post Not Found) \[[0-9a-f]+\] - [a-zA-Z]{8}",
                    scraper_result.page.title,
                )
                or re.fullmatch(
                    r"(Video Disabled) - [a-zA-Z.]{11}",
                    scraper_result.page.title,
                )
            ) is not None:
                link_status = LinkStatus.BROKEN
                error = match.group(1)
        case HTTPStatus.UNAUTHORIZED.value | HTTPStatus.FORBIDDEN.value:
            link_status = LinkStatus.BLOCKED
        case _:
            pass

    if error is not None or (
        scraper_result.response.ok
        and scraper_result.response.status_code not in REDIRECT_STATUS_CODES
    ):
        return link_status, error

    error = "{} {}".format(  # noqa: UP032
        scraper_result.response.status_code,
        scraper_result.response.reason,
    )

    if link_status != LinkStatus.OK:
        return link_status, error

    link_status = LinkStatus.POSSIBLY_BROKEN

    return link_status, error


def _fix_url_quoting(
    url: ParseResult,
    **_: Any,
) -> ParseResult:
    return url._replace(path=quote(url.path))


def _fix_url_subdomain(
    url: ParseResult,
    redirect_url: ParseResult,
) -> ParseResult:
    if (
        url.hostname is None
        or redirect_url.hostname is None
        or url.hostname == redirect_url.hostname
    ):
        return url

    original_domain = _get_tld_result_lax(url.hostname)
    redirect_domain = _get_tld_result_lax(redirect_url.hostname)

    if (
        original_domain is None
        or redirect_domain is None
        or original_domain.fld != redirect_domain.fld
        or _remove_www_subdomain(original_domain.subdomain)
        != _remove_www_subdomain(redirect_domain.subdomain)
    ):
        return url

    return url._replace(netloc=redirect_url.netloc)


def _fix_url_trailing_slash(
    url: ParseResult,
    **_: Any,
) -> ParseResult:
    return url._replace(
        path=(
            url.path.rstrip("/") if url.path.endswith("/") else f"{url.path}/"
        ),
    )


class _FixerCallable(Protocol):
    def __call__(
        self,
        url: ParseResult,
        *,
        redirect_url: ParseResult,
    ) -> ParseResult: ...


_URL_FIXERS: list[_FixerCallable] = [
    _fix_url_quoting,
    _fix_url_trailing_slash,
    _fix_url_subdomain,
]


def get_fixed_url(response: scraper.Response, url: str) -> None | str:
    if response.status_code in NOT_FOUND_STATUS_CODES:
        return _fix_url_trailing_slash(urlparse(url)).geturl()

    if (
        response.redirect_url is None
        or response.status_code not in REDIRECT_STATUS_CODES
    ):
        return None

    parsed_url = urlparse(url)
    parsed_redirect_url = urlparse(response.redirect_url)

    for length in range(1, len(_URL_FIXERS) + 1):
        for fixer_combination in itertools.combinations(_URL_FIXERS, length):
            fixed_parsed_url = parsed_url

            for fixer in fixer_combination:
                fixed_parsed_url = fixer(
                    fixed_parsed_url,
                    redirect_url=parsed_redirect_url,
                )

            if fixed_parsed_url == parsed_redirect_url:
                return fixed_parsed_url.geturl()

    return None
