from enum import IntEnum, unique
from html.parser import HTMLParser
from http import HTTPStatus
import re
from urllib.parse import urlparse

from bookmarkmgr.cronet import RequestError

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

INVALID_HTML_PARENTS = {
    "base",
    "link",
    "meta",
}


@unique
class LinkStatus(IntEnum):
    OK = 1
    BROKEN = 2
    POSSIBLY_BROKEN = 3
    BLOCKED = 4


class _HTMLParser(HTMLParser):
    def reset(self, *args, **kwargs):
        self._path = []
        self.body_text = ""
        self.title = ""

        return super().reset(*args, **kwargs)

    def handle_starttag(
        self,
        tag,
        attrs,  # noqa: ARG002
    ):
        if tag not in INVALID_HTML_PARENTS:
            self._path.append(tag)

    def handle_endtag(self, tag):
        if len(self._path) > 0 and self._path[-1] == tag:
            self._path.pop()

    def handle_data(self, data):
        match self._path:
            case ["html", "body"]:
                self.body_text = data.strip()
            case ["html", "head", "title"]:
                self.title = data.strip()


async def check_is_link_broken(session, url, *, fix_broken=True):  # noqa: C901
    html_parser = None

    def retry_predicate(response):
        if response.status_code != HTTPStatus.OK.value:
            return False

        nonlocal html_parser

        if html_parser is None:
            html_parser = _HTMLParser()
        else:
            html_parser.reset()

        html_parser.feed(response.text)

        return html_parser.body_text == "Loading..."  # Rate limit hit

    link_status = LinkStatus.OK
    error = None
    fixed_url = None

    try:
        response = await session.get(
            url,
            allow_redirects=False,
            retry_predicate=retry_predicate,
        )
    except RequestError as error:
        link_status = LinkStatus.POSSIBLY_BROKEN
        error = str(error)

        return link_status, error, fixed_url

    match response.status_code:
        case 200:
            if html_parser.title == "Video deleted":
                link_status = LinkStatus.BROKEN
                error = html_parser.title
            elif (
                match := re.fullmatch(
                    r"(Post Not Found) \[[0-9a-f]+\] - [a-zA-Z]{8}",
                    html_parser.title,
                )
            ) is not None:
                link_status = LinkStatus.BROKEN
                error = match.group(1)
            elif (
                match := re.fullmatch(
                    r"(Video Disabled) - [a-zA-Z]{7}\.[a-z]{3}",
                    html_parser.title,
                )
            ) is not None:
                link_status = LinkStatus.POSSIBLY_BROKEN
                error = match.group(1)
        case 401 | 403:
            link_status = LinkStatus.BLOCKED

    if error is not None or (
        response.ok and response.status_code not in REDIRECT_STATUS_CODES
    ):
        return link_status, error, fixed_url

    error = f"{response.status_code} {response.reason}"

    if link_status != LinkStatus.OK:
        return link_status, error, fixed_url

    link_status = LinkStatus.POSSIBLY_BROKEN

    if not fix_broken or (
        response.status_code
        not in REDIRECT_STATUS_CODES | NOT_FOUND_STATUS_CODES
    ):
        return link_status, error, fixed_url

    # Raindrop breaks some links during import by removing trailing slash and
    # by adding trailing slash during new link addition.
    parsed_url = urlparse(url)
    potentially_fixed_url = parsed_url._replace(
        path=(
            parsed_url.path.rstrip("/")
            if parsed_url.path.endswith("/")
            else f"{parsed_url.path}/"
        ),
    ).geturl()

    if (
        response.status_code in REDIRECT_STATUS_CODES
        and response.redirect_url != potentially_fixed_url
    ):
        return link_status, error, fixed_url

    new_link_status, _, _ = await check_is_link_broken(
        session,
        potentially_fixed_url,
        fix_broken=False,
    )

    if new_link_status == LinkStatus.OK:
        fixed_url = potentially_fixed_url

    return link_status, error, fixed_url
